"""HTTPS, DNS pinning, robots and explicit redirect handling for CEICE."""
from contextlib import contextmanager
import ipaddress
import re
import socket
import time
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit

import httpx

ALLOWED_HOSTS=frozenset({'ceice.gva.es'})
REDIRECTS={301,302,303,307,308}
_UNRESERVED=frozenset('ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~')


class PolicyError(httpx.HTTPError):
    pass


class ResponseDecodingError(httpx.DecodingError):
    """Safe response metadata for a failed HTTP content decoding."""
    def __init__(self, url, status_code, content_encoding, cause_type):
        super().__init__('response_decoding_failed')
        self.url = url
        self.status_code = status_code
        self.content_encoding = content_encoding
        self.cause_type = cause_type


def _robots_octets(value):
    """Normalize unreserved escapes while preserving encoded reserved octets."""
    result=[]; i=0
    while i<len(value):
        char=value[i]
        if char=='%':
            if i+2>=len(value) or not re.fullmatch('[0-9a-fA-F]{2}',value[i+1:i+3]):
                raise PolicyError('invalid_robots_percent_encoding')
            byte=int(value[i+1:i+3],16); decoded=chr(byte)
            result.append(decoded if decoded in _UNRESERVED else '%%%02X' % byte)
            i+=3
        elif ord(char)>127:
            result.extend('%%%02X' % byte for byte in char.encode('utf-8'))
            i+=1
        else:
            result.append(char); i+=1
    return ''.join(result)


def _robots_rule_length(rule):
    """Count encoded bytes as one octet; '*' does not increase specificity."""
    return len(re.sub(r'%[0-9A-F]{2}','x',rule.replace('*','')))


class PolicyClient:
    def __init__(self, *, user_agent, timeout=30.0, max_bytes=50*1024*1024):
        self.user_agent=user_agent
        self.timeout=timeout
        self.max_bytes=max_bytes
        self._robots={}
        self._last_request=0.0
        self._delay=.25

    def close(self):
        pass  # Each exchange owns and closes its own httpx client.

    def _url(self, url):
        try:
            p=urlsplit(url)
            if (p.scheme!='https' or p.hostname not in ALLOWED_HOSTS or p.username is not None
                    or p.password is not None or p.port not in (None,443) or p.fragment
                    or not p.path.startswith('/') or '\\' in url or any(ord(c)<32 for c in url)):
                raise ValueError
            if re.search(r'%(?![0-9a-fA-F]{2})',p.path):
                raise ValueError
            decoded=unquote(p.path,errors='strict')
            if (any(part in {'.','..'} for part in decoded.split('/')) or '//' in decoded
                    or '\\' in decoded or '%' in decoded or any(ord(c)<32 for c in decoded)):
                raise ValueError
            return urlunsplit(('https',p.hostname,p.path,p.query,''))
        except (ValueError,TypeError,UnicodeError):
            raise PolicyError('unapproved_url') from None

    def _resolve(self, host):
        try:
            records=[entry[4][0] for entry in socket.getaddrinfo(host,443,type=socket.SOCK_STREAM)]
            ips=[ipaddress.ip_address(ip) for ip in records]
            if not ips or any(not ip.is_global for ip in ips):
                raise ValueError
            return str(ips[0])
        except (OSError,ValueError):
            raise PolicyError('unapproved_dns') from None

    def _send(self, url, headers, limit):
        host=urlsplit(url).hostname
        ip=self._resolve(host)
        elapsed=time.monotonic()-self._last_request
        if elapsed < self._delay: time.sleep(self._delay-elapsed)
        self._last_request=time.monotonic()
        return self._exchange(url,headers,limit,ip)

    def _exchange(self, url, headers, limit, ip):
        host=urlsplit(url).hostname
        p=urlsplit(url); authority='['+ip+']' if ':' in ip else ip
        pinned=urlunsplit(('https',authority,p.path,p.query,''))
        try:
            with httpx.Client(timeout=self.timeout,follow_redirects=False,trust_env=False) as client:
                with client.stream('GET',pinned,headers={**headers,'Host':host},
                                   extensions={'sni_hostname':host}) as response:
                    encoding=response.headers.get('content-encoding','')
                    encoding=encoding[:128] if re.fullmatch(r'[a-zA-Z0-9, ._-]{0,128}',encoding) else 'invalid'
                    safe_url=urlunsplit((p.scheme,p.netloc,p.path,'',''))
                    if (any(k.lower()=='accept-encoding' and v.lower()=='identity' for k,v in headers.items())
                            and encoding.lower() not in ('','identity')):
                        raise ResponseDecodingError(safe_url,response.status_code,encoding,'UnexpectedContentEncoding')
                    content=[]; total=0
                    try:
                        for chunk in response.iter_bytes():
                            total+=len(chunk)
                            if total>limit: raise PolicyError('response_size_limit')
                            content.append(chunk)
                    except httpx.DecodingError as exc:
                        cause=type(exc.__cause__).__name__ if exc.__cause__ is not None else None
                        raise ResponseDecodingError(safe_url,response.status_code,encoding or None,cause) from exc
                    # iter_bytes has already decoded the body. Keeping these wire
                    # headers would cause the new Response to decode it a second time.
                    response_headers=httpx.Headers(response.headers)
                    if 'content-encoding' in response_headers:
                        del response_headers['content-encoding']
                        response_headers.pop('content-length',None)
                    return httpx.Response(response.status_code,headers=response_headers,
                        content=b''.join(content),request=httpx.Request('GET',url))
        except httpx.HTTPError:
            raise

    def _allowed_by_robots(self, url):
        host=urlsplit(url).hostname
        if host not in self._robots:
            response=self._send('https://'+host+'/robots.txt',{'User-Agent':self.user_agent},512*1024)
            if response.status_code!=200: raise PolicyError('robots_unavailable')
            groups=[]; agents=[]; rules=[]; had_rules=False
            for raw in response.text.splitlines():
                line=raw.split('#',1)[0].strip()
                if not line or ':' not in line: continue
                name,value=(x.strip() for x in line.split(':',1)); name=name.lower()
                if name=='user-agent':
                    if had_rules:
                        groups.append((agents,rules)); agents=[]; rules=[]; had_rules=False
                    agents.append(value.lower())
                elif name in {'allow','disallow'} and agents:
                    had_rules=True
                    if value: rules.append((name,value))
                elif name=='crawl-delay' and agents:
                    try:
                        delay=float(value)
                        if not 0<=delay<=60: raise ValueError
                        self._delay=max(self._delay,delay)
                    except ValueError:
                        raise PolicyError('unsupported_robots_delay') from None
            if agents: groups.append((agents,rules))
            # Exclude the contact comment but retain multi-word identifiers,
            # such as the explicit "ChatGPT Agent" group used by CEICE.
            product=self.user_agent.split('(',1)[0].strip().lower()
            selected=[]; specificity=-1
            for agents,rules in groups:
                scores=[0 if a=='*' else len(a) for a in agents if a and (a=='*' or a in product)]
                if not scores: continue
                score=max(scores)
                if score>specificity: selected=[]; specificity=score
                if score==specificity: selected.extend(rules)
            if specificity<0:
                raise PolicyError('robots_no_applicable_group')
            self._robots[host]=selected
        path=_robots_octets(urlsplit(url).path + (('?' + urlsplit(url).query) if urlsplit(url).query else ''))
        matches=[]
        for kind,rule in self._robots[host]:
            rule=_robots_octets(rule); anchored=rule.endswith('$')
            if anchored: rule=rule[:-1]
            pattern='^'+re.escape(rule).replace(r'\*','.*')+('$' if anchored else '')
            if re.search(pattern,path): matches.append((_robots_rule_length(rule),kind=='allow'))
        return not matches or max(matches)[1]

    @contextmanager
    def stream(self, method, url, headers=None, *, max_bytes=None):
        if method!='GET': raise PolicyError('method_not_allowed')
        if any(key.lower()=='user-agent' for key in (headers or {})):
            raise PolicyError('user_agent_override_forbidden')
        current=self._url(url); seen=set()
        for _ in range(6):
            if current in seen: raise PolicyError('redirect_loop')
            seen.add(current)
            if not self._allowed_by_robots(current): raise PolicyError('robots_disallowed')
            response=self._send(current,{'User-Agent':self.user_agent,**(headers or {})},max_bytes or self.max_bytes)
            if response.status_code not in REDIRECTS:
                yield response
                return
            location=response.headers.get('location')
            if not location: raise PolicyError('redirect_missing_location')
            current=self._url(urljoin(current,location))
        raise PolicyError('redirect_limit')
