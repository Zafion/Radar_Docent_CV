"""DR04: synthetic manifests only; never open a real database."""
import contextlib
import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from scripts import import_remote_fetch as importer

ROOT = Path(__file__).resolve().parents[1]


def fixture(root, content=b'%PDF-1.4 synthetic\n'):
    incoming = root/'incoming'; (incoming/'files').mkdir(parents=True)
    (incoming/'files/doc.pdf').write_bytes(content)
    item = {'status':'downloaded', 'sha256':hashlib.sha256(content).hexdigest(),
            'size_bytes':len(content), 'stored_filename':'doc.pdf','original_filename':'doc.pdf',
            'relative_file_path':'files/doc.pdf', 'content_type':'application/pdf',
            'asset':{'source_key':'synthetic','source_url':'https://example.invalid',
                     'asset_role':'pdf_candidate','title':'Synthetic','url':'https://example.invalid/doc.pdf',
                     'canonical_url':'https://example.invalid/doc.pdf','downloadable':True}}
    manifest={'schema_version':1,'sources':[{'source_key':'synthetic','source_url':'https://example.invalid',
                  'status':'success','results':[item]}]}
    return incoming, manifest, item


def run_import(root, incoming, manifest, store=None, intent='historical_import', known=None):
    path=incoming/'manifest.json';path.write_text(json.dumps(manifest))
    store=store or Mock()
    store.get_document_version_by_sha256.return_value=known
    store.create_document_version.return_value=1
    with patch.object(importer,'SyncStore',return_value=store),contextlib.redirect_stdout(io.StringIO()):
        result=importer.import_manifest(manifest_path=path,incoming_dir=incoming,data_dir=root/'data',notification_intent=intent)
    return result,store


class RemoteIntegrityTests(unittest.TestCase):
    def test_wrong_sha_is_rejected_before_database(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            root=Path(tmp);incoming,manifest,item=fixture(root);item['sha256']='0'*64
            path=incoming/'manifest.json';path.write_text(json.dumps(manifest))
            with patch.object(importer,'SyncStore') as factory:
                with self.assertRaises(ValueError):
                    importer.import_manifest(manifest_path=path,incoming_dir=incoming,data_dir=root/'data')
                factory.assert_not_called()

    def test_wrong_size_is_rejected(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            root=Path(tmp);incoming,manifest,item=fixture(root);item['size_bytes']+=1
            with self.assertRaises(ValueError):run_import(root,incoming,manifest)




class RemoteIntegrityMatrixTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=ROOT);self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.incoming,self.manifest,self.item=fixture(self.root)

    def run_import(self, store=None):
        return run_import(self.root,self.incoming,self.manifest,store)

    def test_valid_bytes_are_published_and_recorded(self):
        result,store=self.run_import()
        self.assertEqual(result['new_versions_count'],1)
        args=store.create_document_version.call_args.kwargs
        self.assertEqual(Path(args['file_path']).read_bytes(),(self.incoming/'files/doc.pdf').read_bytes())
        self.assertEqual(args['sha256'],self.item['sha256'])
        self.assertEqual(args['notification_intent'],'historical_import')
        store.connection.commit.assert_called_once()

    def test_missing_size_and_invalid_hash_are_not_guessed(self):
        for field,value in [('size_bytes',None),('size_bytes',True),('sha256','knownhash')]:
            with self.subTest(field=field,value=value):
                original=self.item[field];self.item[field]=value
                with self.assertRaises(ValueError):self.run_import()
                self.item[field]=original

    def test_truncated_missing_and_unreadable_files(self):
        from app.services import remote_import_integrity as integrity
        path=self.incoming/'files/doc.pdf';content=path.read_bytes()
        path.write_bytes(content[:-1])
        with self.assertRaises(ValueError):self.run_import()
        path.unlink()
        with self.assertRaises(ValueError):self.run_import()
        path.write_bytes(content)
        with patch.object(integrity,'regular',side_effect=PermissionError):
            with self.assertRaises(ValueError):self.run_import()

    def test_bad_paths_and_symlinks_never_escape(self):
        for field,value in [('relative_file_path','../outside.pdf'),('relative_file_path','/etc/passwd'),
                            ('stored_filename','../outside.pdf'),('stored_filename','/tmp/out.pdf')]:
            with self.subTest(field=field):
                old=self.item[field];self.item[field]=value
                with self.assertRaises(ValueError):self.run_import()
                self.item[field]=old
        outside=self.root/'outside.pdf';outside.write_bytes((self.incoming/'files/doc.pdf').read_bytes())
        (self.incoming/'files/doc.pdf').unlink();(self.incoming/'files/doc.pdf').symlink_to(outside)
        with self.assertRaises(ValueError):self.run_import()
        (self.incoming/'files/doc.pdf').unlink();(self.incoming/'files').rmdir()
        (self.incoming/'files').symlink_to(self.root,target_is_directory=True)
        self.item['relative_file_path']='files/outside.pdf'
        with self.assertRaises(ValueError):self.run_import()

    def test_source_and_destination_directories_reject_symlinks(self):
        original=self.manifest['sources'][0]['source_key']
        self.manifest['sources'][0]['source_key']='../escape'
        with self.assertRaises(ValueError):self.run_import()
        self.manifest['sources'][0]['source_key']=original
        data=self.root/'data';data.mkdir(exist_ok=True);(data/'synthetic').symlink_to(self.incoming,target_is_directory=True)
        with self.assertRaises(ValueError):self.run_import()
        self.assertFalse((self.incoming/'doc.pdf').exists())

    def test_snapshot_prevents_source_substitution_between_validation_and_use(self):
        original=importer.publish_snapshot
        expected=(self.incoming/'files/doc.pdf').read_bytes()
        def replaced(snapshot,**kwargs):
            path=self.incoming/'files/doc.pdf';path.unlink();path.write_bytes(b'wrong replacement')
            return original(snapshot,**kwargs)
        with patch.object(importer,'publish_snapshot',side_effect=replaced):
            _,store=self.run_import()
        self.assertEqual(Path(store.create_document_version.call_args.kwargs['file_path']).read_bytes(),expected)

    def test_in_place_mutation_during_read_is_rejected(self):
        from app.services import remote_import_integrity as integrity
        original=integrity.check_bytes
        def mutating(handle,sha,size,destination=None):
            if destination is not None:
                source_handle=handle
                class ChangedReader:
                    def fileno(inner):return source_handle.fileno()
                    def seek(inner,*args):return source_handle.seek(*args)
                    def read(inner,n):
                        chunk=source_handle.read(n)
                        if chunk:
                            with (self.incoming/'files/doc.pdf').open('ab') as changed:changed.write(b'!')
                        return chunk
                handle=ChangedReader()
            return original(handle,sha,size,destination)
        with patch.object(integrity,'check_bytes',side_effect=mutating):
            with self.assertRaises(ValueError):self.run_import()

    def test_existing_destination_is_not_overwritten(self):
        dest=self.root/'data/synthetic/files';dest.mkdir(parents=True)
        target=dest/'doc.pdf';target.write_bytes(b'previous correct data')
        with self.assertRaises(ValueError):self.run_import()
        self.assertEqual(target.read_bytes(),b'previous correct data')

    def test_existing_destination_symlink_is_rejected(self):
        dest=self.root/'data/synthetic/files';dest.mkdir(parents=True)
        outside=self.root/'outside';outside.write_bytes(b'unchanged')
        (dest/'doc.pdf').symlink_to(outside)
        with self.assertRaises(ValueError):self.run_import()
        self.assertEqual(outside.read_bytes(),b'unchanged')

    def test_conflicting_and_identical_manifest_duplicates(self):
        duplicate=copy.deepcopy(self.item);self.manifest['sources'][0]['results'].append(duplicate)
        duplicate['sha256']='0'*64
        with self.assertRaises(ValueError):self.run_import()
        duplicate['sha256']=self.item['sha256'];duplicate['status']='same_run_duplicate'
        result,store=self.run_import()
        self.assertEqual(store.create_document_version.call_count,1)
        self.assertEqual(result['duplicate_assets_count'],1)
        self.assertEqual(store.set_asset_document_version.call_count,2)

    def test_duplicate_json_keys_rejected(self):
        path=self.incoming/'manifest.json';path.write_text('{"schema_version":1,"schema_version":1,"sources":[]}')
        with self.assertRaises(ValueError):
            importer.import_manifest(manifest_path=path,incoming_dir=self.incoming,data_dir=self.root/'data')

    def test_known_hash_still_requires_received_file(self):
        path=self.incoming/'files/doc.pdf';path.unlink()
        store=Mock();store.get_document_version_by_sha256.return_value={'id':1}
        with self.assertRaises(ValueError):self.run_import(store)
        store.ensure_source.assert_not_called()

    def test_whole_batch_preflight_prevents_partial_registration(self):
        duplicate=copy.deepcopy(self.item);duplicate['asset']['canonical_url']+='?other=1';duplicate['sha256']='0'*64
        self.manifest['sources'][0]['results'].append(duplicate)
        store=Mock()
        with self.assertRaises(ValueError):self.run_import(store)
        store.create_document_version.assert_not_called();store.ensure_source.assert_not_called()
        self.assertFalse((self.root/'data/synthetic/files/doc.pdf').exists())

    def test_copy_failure_rolls_back_and_removes_staging(self):
        from app.services import remote_import_integrity as integrity
        with patch.object(integrity.os,'link',side_effect=OSError('synthetic interruption')):
            store=Mock()
            with self.assertRaises(ValueError):self.run_import(store)
        store.connection.rollback.assert_called_once()
        self.assertEqual(list((self.root/'data').rglob('.import-*')),[])
        store.create_document_version.assert_not_called()

    def test_database_failure_preserves_published_valid_bytes_for_retry(self):
        store=Mock();store.create_document_version.side_effect=RuntimeError('synthetic persistence error')
        with self.assertRaises(RuntimeError):self.run_import(store)
        store.connection.rollback.assert_called_once()
        target=self.root/'data/synthetic/files/doc.pdf'
        self.assertEqual(hashlib.sha256(target.read_bytes()).hexdigest(),self.item['sha256'])
        self.run_import()  # validated orphan can safely be reused
        self.assertEqual(list((self.root/'data').rglob('.import-*')),[])


    def test_known_version_reuses_identity_and_preserves_intent(self):
        _,created=self.run_import()
        known={'id':7, **created.create_document_version.call_args.kwargs}
        store=Mock()
        result,_=run_import(self.root,self.incoming,self.manifest,store,intent='live_new',known=known)
        self.assertEqual(result['known_versions_count'],1)
        store.create_document_version.assert_not_called()
        store.set_asset_document_version.assert_called_once_with(asset_id=store.create_asset.return_value,document_version_id=7)

    def test_corrupt_known_destination_is_never_used_or_overwritten(self):
        _,created=self.run_import()
        known={'id':7, **created.create_document_version.call_args.kwargs}
        dest=Path(known['file_path']);dest.write_bytes(b'CORRUPTED')
        store=Mock()
        with self.assertRaises(ValueError):
            run_import(self.root,self.incoming,self.manifest,store,known=known)
        self.assertEqual(dest.read_bytes(),b'CORRUPTED')
        store.connection.rollback.assert_called_once()
        store.set_asset_document_version.assert_not_called()

    def test_manifest_symlink_and_nonregular_input_are_rejected(self):
        from app.services import remote_import_integrity as integrity
        import os
        path=self.incoming/'manifest.json';external=self.root/'outside.json'
        external.write_text(json.dumps(self.manifest));path.symlink_to(external)
        with self.assertRaises(ValueError):
            importer.import_manifest(manifest_path=path,incoming_dir=self.incoming,data_dir=self.root/'data')
        path.unlink();received=self.incoming/'files/doc.pdf';received.unlink();os.mkfifo(received)
        with self.assertRaises(ValueError):self.run_import()

    def test_cli_rejection_returns_nonzero_and_safe_reason(self):
        self.item['sha256']='0'*64
        (self.incoming/'manifest.json').write_text(json.dumps(self.manifest))
        out=io.StringIO()
        with patch('sys.argv',['import_remote_fetch','--incoming-dir',str(self.incoming),'--data-dir',str(self.root/'data')]),contextlib.redirect_stdout(out),patch.object(importer,'SyncStore') as factory:
            self.assertEqual(importer.main(),2)
        factory.assert_not_called()
        self.assertEqual(json.loads(out.getvalue()),{'status':'rejected','rejected_batches':1,'reason':'sha256_mismatch'})


if __name__ == "__main__": unittest.main()
