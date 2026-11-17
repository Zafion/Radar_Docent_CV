"""Preview selection without writes, or execute an explicitly authorized SHA plan."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path

from app.services.parse_plan import preview, execute_plan


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--preview', action='store_true')
    action.add_argument('--execute-plan', type=Path)
    parser.add_argument('--reprocess', action='store_true')
    parser.add_argument('--sha-file', type=Path, help='One complete lowercase SHA256 per line; max 1000.')
    parser.add_argument('--parser-key')
    parser.add_argument('--family')
    parser.add_argument('--authorize-reprocess', action='store_true')
    args = parser.parse_args(argv)
    if args.preview:
        if args.authorize_reprocess:
            parser.error('authorization is only meaningful with --execute-plan')
        shas = args.sha_file.read_text().splitlines() if args.sha_file else None
        result = preview(shas=shas, parser_key=args.parser_key, family=args.family, reprocess=args.reprocess)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.reprocess or args.sha_file or args.parser_key or args.family:
        parser.error('execution uses the reviewed plan only; do not add selection overrides')
    result = execute_plan(json.loads(args.execute_plan.read_text()), authorized=args.authorize_reprocess)
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    return int(any(s.failed_count for s in result.parser_summaries))


if __name__ == '__main__':
    raise SystemExit(main())
