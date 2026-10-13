import argparse
import json
import os
import sys
from pathlib import Path

from .engine import validate_campaign
from .service import Service


def main():
    parser = argparse.ArgumentParser(description='Instagram Outreach: Import, Analyse, Entwürfe und Review.')
    parser.add_argument('--data-dir', default=os.getenv('OUTREACH_DATA_DIR'), help='Absoluter Pfad für SQLite und Betriebsdaten')
    parser.add_argument('--campaign', default=str(Path(__file__).resolve().parent.parent / 'config' / 'general.json'))
    sub = parser.add_subparsers(dest='command', required=True)
    web = sub.add_parser('serve')
    web.add_argument('--host', default='127.0.0.1')
    web.add_argument('--port', type=int, default=8787)
    imp = sub.add_parser('import')
    imp.add_argument('file', help='Apify-Export oder normalisierte Profile als JSON-Liste')
    apify = sub.add_parser('apify')
    apify.add_argument('dataset_id')
    apify.add_argument('--max-items', type=int, default=100)
    gen = sub.add_parser('generate')
    gen.add_argument('--username')
    gen.add_argument('--provider', choices=['template', 'openai'], default='template')
    sub.add_parser('work', help='Bereite alle derzeit fälligen Aufgaben vor')
    sub.add_parser('list')
    sub.add_parser('stats')
    export = sub.add_parser('export')
    export.add_argument('file', help='CSV-Ziel, nur freigegebene Nachrichten')
    for action in ('approve', 'sent', 'replied', 'suppress'):
        p = sub.add_parser(action)
        p.add_argument('username')
        if action == 'approve':
            p.add_argument('draft_id')
    args = parser.parse_args()
    if not args.data_dir or not Path(args.data_dir).is_absolute():
        parser.error('--data-dir oder OUTREACH_DATA_DIR muss ein absoluter Pfad sein.')
    if str(Path(__file__).resolve()).startswith('/Volumes/Lexar/') and not str(Path(args.data_dir).resolve()).startswith('/Volumes/Lexar/'):
        parser.error('Für diese Lexar-Installation muss auch das Datenverzeichnis auf Lexar liegen.')
    campaign = validate_campaign(json.loads(Path(args.campaign).read_text(encoding='utf-8')))
    service = Service(args.data_dir, campaign)
    if args.command == 'serve':
        from .server import serve
        token = os.getenv('OUTREACH_ACCESS_TOKEN')
        if args.host not in ('127.0.0.1', 'localhost', '::1') and (not token or len(token) < 32):
            parser.error('Für Netzwerkzugriff OUTREACH_ACCESS_TOKEN mit mindestens 32 Zeichen setzen und HTTPS vorschalten.')
        return serve(service, args.host, args.port, token)
    if args.command == 'import':
        result = service.import_profiles(json.loads(Path(args.file).read_text(encoding='utf-8')))
    elif args.command == 'apify':
        from .providers import import_apify_dataset
        if not 1 <= args.max_items <= 1000:
            parser.error('--max-items muss zwischen 1 und 1000 liegen.')
        result = service.import_profiles(import_apify_dataset(args.dataset_id, max_items=args.max_items))
    elif args.command == 'generate':
        names = [args.username] if args.username else [p['username'] for p in service.list_profiles() if p['status'] in ('pending', 'review') and not p.get('drafts') and (p.get('analysis') or {}).get('eligible')]
        result = [service.enqueue(name, provider=args.provider) for name in names]
    elif args.command == 'work':
        count = 0
        while service.run_once():
            count += 1
        result = {'processed': count, 'jobs': service.jobs()}
    elif args.command == 'list':
        result = service.list_profiles()
    elif args.command == 'stats':
        result = service.stats()
    elif args.command == 'export':
        from .server import export_csv
        target = Path(args.file).resolve()
        target.write_bytes(export_csv(service.export_rows()))
        result = {'file': str(target)}
    elif args.command == 'approve':
        result = service.approve(args.username, args.draft_id)
    else:
        result = {'sent': service.mark_sent, 'replied': service.mark_replied, 'suppress': service.suppress}[args.command](args.username)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError) as exc:
        print('Fehler: ' + str(exc), file=sys.stderr)
        sys.exit(1)
