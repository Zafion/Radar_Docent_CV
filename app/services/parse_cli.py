"""Legacy scheduled entrypoints are automatic-only; reject ambiguous flags."""
import argparse


def automatic_entrypoint():
    argparse.ArgumentParser(
        description='Automatic first live attempts only. Use run_parse_selection.py for read-only previews and reviewed SHA plans.'
    ).parse_args()
