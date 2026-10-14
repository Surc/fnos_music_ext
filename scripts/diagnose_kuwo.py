#!/usr/bin/env python3
"""Read-only local musicdl diagnostics; never print or follow signed audio URLs."""
import argparse
import json
import re
import time
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--service-url', default='http://127.0.0.1:8768')
    parser.add_argument('--keyword', default="DAYBREAK'S BELL")
    parser.add_argument('--id', default='kuwo:142276943')
    parser.add_argument('--timeout', type=float, default=45)
    parser.add_argument('--proxy', action='store_true', help='Also read at most 1024 bytes via /stream?proxy=true')
    args = parser.parse_args()
    base = args.service_url.rstrip('/')
    parsed = urlsplit(base)
    if (parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment):
        parser.error('--service-url must be an HTTP(S) base URL without credentials or query')
    # This request goes to the NAS service itself; do not send it through a system proxy.
    opener = build_opener(ProxyHandler({}), NoRedirect())

    def call(stage, path, params=None, *, audio=False):
        started = time.monotonic()
        result = {'stage': stage}
        payload = {}
        try:
            request = Request(base + path + ('?' + urlencode(params) if params else ''),
                              headers={'Range': 'bytes=0-1023'} if audio else {})
            with opener.open(request, timeout=args.timeout) as response:
                result['status'] = response.status
                if audio:
                    result['bytes_read'] = len(response.read(1024))
                    kind = response.headers.get('Content-Type', '').split(';')[0].lower()
                    result['audio_content_type'] = kind if re.fullmatch(r'audio/[a-z0-9.+-]+', kind) else 'other'
                else:
                    payload = json.loads(response.read(2 * 1024 * 1024))
        except HTTPError as exc:
            result['status'] = exc.code
            exc.close()  # do not read the body or print Location, which may contain a token
        except Exception as exc:
            result['error'] = type(exc).__name__
        result['elapsed_s'] = round(time.monotonic() - started, 3)
        return result, payload

    result, health = call('health', '/healthz')
    result.update({key: health[key] for key in ('sources', 'breaker_open', 'timeouts', 'adaptive_timeouts') if key in health})
    print(json.dumps(result, ensure_ascii=False))
    result, search = call('search', '/search', {'keyword': args.keyword, 'sources': 'kuwo', 'limit': 30})
    items = search.get('items') or []
    target = next((item for item in items if item.get('id') == args.id), {})
    result.update(count=len(items), target_found=bool(target), source_errors=list((search.get('errors') or {}).keys()))
    print(json.dumps(result, ensure_ascii=False))
    if not target:
        return 1
    result, _ = call('resolve', '/stream', {'id': args.id})
    verified = result.get('status') == 302
    print(json.dumps(result, ensure_ascii=False))
    result, info = call('info', '/info', {'id': args.id})
    result.update({key: info[key] for key in ('id', 'duration_s', 'playback_status', 'verified') if key in info})
    print(json.dumps(result, ensure_ascii=False))
    if args.proxy and verified:
        result, _ = call('proxy_bytes', '/stream', {'id': args.id, 'proxy': 'true'}, audio=True)
        verified = result.get('status') in (200, 206) and result.get('bytes_read', 0) > 0
        print(json.dumps(result, ensure_ascii=False))
    return 0 if verified else 1


if __name__ == '__main__':
    raise SystemExit(main())
