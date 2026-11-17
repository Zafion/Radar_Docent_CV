"""Subprocess-only fake provider. Not selectable by production configuration."""
import json,os,stat,sys,time
from pathlib import Path

if __name__=='__main__':
    request=json.load(sys.stdin)
    scenario=request.get('scenario') or request.get('body') or request.get('text') or 'ok'
    if request.get('started_marker'):Path(request['started_marker']).write_text('started')
    inherited_socket=False
    for path in Path('/proc/self/fd').iterdir():
        try:inherited_socket |= stat.S_ISSOCK(os.fstat(int(path.name)).st_mode)
        except OSError:pass
    if scenario=='never':time.sleep(60)
    if scenario=='slow':time.sleep(request.get('delay',.2))
    if scenario=='error':raise RuntimeError('synthetic_connection_error')
    if request.get('accepted_marker'):Path(request['accepted_marker']).write_text('accepted')
    print(json.dumps(dict(ok=True,result={'message_id':1},inherited_socket=inherited_socket,
                          inherited_database_env=any(k.startswith(('PG','DATABASE_')) or k=='RADAR_DOCENT_DB_URL' for k in os.environ))))
