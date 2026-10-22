"""Use OS/environment HTTP proxies while keeping local services direct."""
from ipaddress import ip_address
from urllib.parse import urlsplit
from urllib.request import getproxies, proxy_bypass


def system_proxy(url):
    parsed = urlsplit(str(url))
    host = parsed.hostname or ''
    if host.rstrip('.').lower() == 'localhost':
        return None
    try:
        if ip_address(host).is_loopback:
            return None
    except ValueError:
        pass
    if parsed.scheme not in {'http', 'https'} or proxy_bypass(host):
        return None
    proxy = getproxies().get(parsed.scheme)
    if proxy and '://' not in proxy:
        proxy = 'http://' + proxy
    return proxy or None


class SystemProxySession:
    """Share aiohttp's connection pool and select a proxy for each request."""
    def __init__(self, session):
        self.session = session

    def request(self, method, url, **kwargs):
        kwargs.setdefault('proxy', system_proxy(url))
        return self.session.request(method, url, **kwargs)

    def get(self, url, **kwargs):
        return self.request('GET', url, **kwargs)

    def post(self, url, **kwargs):
        return self.request('POST', url, **kwargs)

    def ws_connect(self, url, **kwargs):
        # ComfyUI WebSocket connections keep their existing direct route.
        return self.session.ws_connect(url, **kwargs)
