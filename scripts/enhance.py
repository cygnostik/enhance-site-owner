#!/usr/bin/env python3
"""Portable Enhance OpenAPI CLI. Python 3.9+, standard library only.

Client guards are accident prevention, NOT an authorization boundary. Use a
server-enforced restricted Enhance principal. No active schema auto-updates.
"""
import argparse
import collections
import copy
import datetime
import http.client
import ipaddress
import json
import math
import mimetypes
import os
import re
import secrets
import socket
import ssl
import stat
import sys
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent
METHODS = ('get', 'post', 'put', 'patch', 'delete', 'head', 'options', 'trace')
MAX_SCHEMA_BYTES = 32 * 1024 * 1024
ALIASES = {'version': 'orchdVersion', 'status': 'orchdStatus',
           'websites': 'getWebsites', 'customers': 'getOrgCustomers'}

# Reviewed 12.25.8 semantic hazards. Keywords below are EXTRA conservative
# protection, never the sole classification for known state-changing GETs.
# Tuple: (requires private output, documented risk).
GET_RISKS = {
    'getOrgMemberLogin': (True, 'Generates a short-lived one-time login link.'),
    'createOtpSession': (True, 'Creates a login session using OTP and bypasses 2FA.'),
    'ssoToRoundcube': (True, 'Generates an SSO token and redirects; redirects remain refused.'),
    'getPhpMyAdminSSOUrl': (True, 'Creates passwordless database administration access.'),
    'getPhpMyAdminWebsiteSSOUrl': (True, 'Creates passwordless website database administration access.'),
    'getWordpressUserSsoUrl': (True, 'Returns WordPress user SSO access.'),
    'openclawSso': (True, 'Generates an SSO URL and redirect; redirects remain refused.'),
    'downloadSql': (True, 'Creates and compresses a database backup and returns a filesystem path.'),
    'getWordpressInstallations': (False, 'Discovers WordPress installations and inserts database metadata.'),
    'scanImportMigrations': (False, 'Scans server files and creates importer database records; master org only.'),
    'downloadWebsiteBackup': (True, 'Streams a fresh website export with workload and sensitive data exposure.'),
    'getWebsiteDomainSslCert': (True, 'Returns TLS certificate material including the private key field key.'),
    'getWebsiteMailDomainSslCert': (True, 'Returns mail TLS certificate material including the private key field key.'),
    'getImportServerPullDomains': (False, 'Pulls domains from a remote import server; not a passive local listing.'),
}

class CLIError(Exception):
    """Messages must contain no request/response values, secrets or URLs."""
    def __init__(self, message, code='validation', status=None):
        super().__init__(message)
        self.code, self.status = code, status


def same_json_value(left, right):
    """Type-exact equality; bool/int equality must not hide conflicts."""
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(same_json_value(left[k], right[k]) for k in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(same_json_value(a, b) for a, b in zip(left, right))
    return left == right


def parse_json(text, allow_identical_duplicates=False):
    def number(value,kind):
        if len(value)>128:
            raise ValueError()
        result=kind(value)
        if type(result) is float and not math.isfinite(result):
            raise ValueError()
        return result
    def unique(pairs):
        out = {}
        for k, v in pairs:
            if k in out and not (allow_identical_duplicates and same_json_value(out[k], v)):
                raise CLIError('Duplicate JSON object key.')
            out[k] = v
        return out
    try:
        return json.loads(text, object_pairs_hook=unique,
                          parse_int=lambda value: number(value,int),
                          parse_float=lambda value: number(value,float),
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeError, RecursionError):
        raise CLIError('Invalid JSON; values are not included in diagnostics.') from None


def read_bytes(path, limit):
    if type(limit) is not int or limit<=0:
        raise CLIError('Local input size limit must be positive.')
    try:
        fd=os.open(path,os.O_RDONLY|getattr(os,'O_NONBLOCK',0))
        with os.fdopen(fd, 'rb') as f:
            if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
                raise CLIError('Local input must be a regular file (no pipes/devices).')
            data = f.read(limit + 1)
    except OSError:
        raise CLIError('Cannot read the requested local file.') from None
    if len(data) > limit:
        raise CLIError('Local input exceeds the configured size limit.')
    return data


def load_json(path, limit=MAX_SCHEMA_BYTES):
    return parse_json(read_bytes(path, limit))


class Catalog:
    def __init__(self, path=None, document=None):
        self.document = document if document is not None else load_json(path or ROOT / 'assets/openapi.json')
        if not isinstance(self.document, dict) or not isinstance(self.document.get('paths'), dict):
            raise CLIError('OpenAPI document must contain paths.')
        if not str(self.document.get('openapi', '')).startswith('3.0.'):
            raise CLIError('Only OpenAPI 3.0.x documents are supported.')
        self._check_refs(self.document)
        self.operations = {}
        for path, original in self.document['paths'].items():
            if not isinstance(path, str) or not path.startswith('/') or path.startswith('//') or any(x in path for x in ('?', '#', '\\', '\r', '\n')):
                raise CLIError('Unsafe route in schema.')
            item = self.resolve(original)
            for method in METHODS:
                if method not in item:
                    continue
                op = copy.deepcopy(item[method])
                opid = op.get('operationId')
                if not isinstance(opid, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_.-]*', opid) or opid in self.operations:
                    raise CLIError('Missing, invalid or duplicate operationId.')
                op.update(path=path, method=method.upper())
                params = {}
                for value in item.get('parameters', []) + op.get('parameters', []):
                    param = self.resolve(value)
                    if not isinstance(param.get('name'), str) or param.get('in') not in ('path','query','header','cookie'):
                        raise CLIError('Invalid parameter definition.')
                    params[(param['in'], param['name'])] = param
                op['parameters'] = list(params.values())
                op['security'] = op.get('security', self.document.get('security'))
                scheme_names = {name for option in (op['security'] or []) for name in option}
                available = self.document.get('components', {}).get('securitySchemes', {})
                op['security_schemes'] = {name: self.resolve(available[name]) if name in available else {'x-cli-missing': True} for name in sorted(scheme_names)}
                self.operations[opid] = op
        self.version = str(self.document.get('info', {}).get('version', 'unknown'))

    def _check_refs(self, value, depth=0):
        if depth > 100:
            raise CLIError('Schema nesting exceeds limit.')
        if isinstance(value, dict):
            if '$ref' in value:
                self.ref(value['$ref'])
            for item in value.values():
                self._check_refs(item, depth + 1)
        elif isinstance(value, list):
            for item in value:
                self._check_refs(item, depth + 1)

    def ref(self, name):
        if not isinstance(name, str) or not name.startswith('#/'):
            raise CLIError('External or absolute schema references are not supported.')
        out = self.document
        try:
            for part in name[2:].split('/'):
                out = out[part.replace('~1', '/').replace('~0', '~')]
        except (KeyError, TypeError):
            raise CLIError('Unresolved local schema reference.') from None
        if not isinstance(out, dict):
            raise CLIError('Schema reference is not an object.')
        return out

    def resolve(self, value):
        seen = set()
        while isinstance(value, dict) and '$ref' in value:
            name = value['$ref']
            if name in seen:
                raise CLIError('Cyclic reference cannot be resolved at this node.')
            seen.add(name)
            value = self.ref(name)  # OAS 3.0 Reference Object siblings are ignored.
        return copy.deepcopy(value)

    def expand(self, value, seen=(), depth=0):
        if depth > 60:
            raise CLIError('Schema expansion exceeds limit.')
        if isinstance(value, dict):
            if '$ref' in value:
                name = value['$ref']
                if name in seen:
                    return {'$ref': name, 'x-cli-note': 'recursive local reference'}
                return self.expand(self.ref(name), seen + (name,), depth + 1)
            return {k: self.expand(v, seen, depth + 1) for k, v in value.items()}
        if isinstance(value, list):
            return [self.expand(v, seen, depth + 1) for v in value]
        return value

    def operation(self, name):
        name = ALIASES.get(name, name)
        if name not in self.operations:
            raise CLIError('Unknown operationId. Use ops search/describe; arbitrary URLs are not accepted.')
        return self.operations[name]

    def list_ops(self, search=None, tag=None, method=None):
        rows = []
        for name, op in sorted(self.operations.items()):
            if method and op['method'] != method.upper():
                continue
            if tag and tag not in op.get('tags', []):
                continue
            if search and search.lower() not in ' '.join([name, op['path'], op.get('summary','')] + op.get('tags', [])).lower():
                continue
            rows.append({'operationId': name, 'method': op['method'], 'path': op['path'],
                         'tags': op.get('tags', []), 'summary': op.get('summary','')})
        return {'schema_version': self.version, 'total': len(self.operations), 'matched': len(rows),
                'method_counts': dict(sorted(collections.Counter(x['method'] for x in self.operations.values()).items())),
                'matched_method_counts': dict(sorted(collections.Counter(x['method'] for x in rows).items())),
                'tag_counts': dict(sorted(collections.Counter(t for x in self.operations.values() for t in x.get('tags', [])).items())),
                'aliases': {k:v for k,v in ALIASES.items() if v in self.operations}, 'operations': rows}

    def describe(self, name):
        op = self.operation(name)
        return dict(self.expand(op), auth_support=auth_support(op), guidance=guidance(op, self),
                    schema_issues=schema_issues(op, self),
                    validation='Subset: types, required, enum, UUID, numeric/length bounds, arrays, closed properties, composition. Not full JSON Schema or server authorization validation.')


def auth_support(op):
    security = op.get('security')
    if security is None:
        return 'undeclared: configured bearer sent if available; server requirements unverified'
    if not security or {} in security:
        return 'public: bearer not sent'
    scheme = op.get('security_schemes', {}).get('bearerAuth', {})
    if any(set(option) == {'bearerAuth'} and not option['bearerAuth'] for option in security) and scheme.get('type')=='http' and scheme.get('scheme','').lower()=='bearer':
        return 'bearer'
    return 'unsupported: requires session cookie or another auth scheme/combination'


def guidance(op, catalog):
    text = ' '.join((op['operationId'], op['path'], op.get('summary', ''))).lower()
    sensitive = bool(re.search(r'sso|session|login|token|password|credential|secret|download|(?:\blog\b)|(?:\blogs\b)|_log|/log|apikey|api.key|provider.key|registration.key|wp.config|private.key', text))
    responses = catalog.expand(op.get('responses', {}))
    binary = any(media != 'application/json' for status, resp in responses.items() if str(status).startswith('2') for media in resp.get('content', {}))
    unstructured = any(media.get('schema',{}).get('type')=='string' for status,resp in responses.items() if str(status).startswith('2') for media in resp.get('content',{}).values())
    if op['operationId'] in ('orchdVersion','orchdStatus','getClientIp','getWordpressLatestVersion'):
        unstructured = False
    risk = GET_RISKS.get(op['operationId'])
    sensitive = sensitive or unstructured or bool(risk and risk[0])
    side_effect = op['method'] != 'GET' or sensitive or binary or risk is not None
    return {'requires_apply': side_effect, 'requires_output': sensitive or binary,
            'reviewed_risk': risk[1] if risk else None,
            'sensitive': sensitive, 'reason': 'Mutation, sensitive GET, log/download, or conservatively flagged side effect.' if side_effect else 'GET read; runtime flag checks still apply.',
            'warning': 'Client scope guards are not a security boundary. Use a genuinely restricted Enhance principal.',
            'pagination': 'Manual only. Use described parameters exactly; no inferred page loops or total assumptions.'}


def schema_issues(op, catalog):
    issues=[]
    declared={p['name'] for p in op['parameters'] if p['in']=='path'}
    placeholders=set(re.findall(r'\{([^{}]+)\}',op['path']))
    if placeholders-declared:
        issues.append({'kind':'missing_path_parameter_declarations','names':sorted(placeholders-declared)})
    if declared-placeholders:
        issues.append({'kind':'declared_path_names_not_in_route','names':sorted(declared-placeholders)})
    content=catalog.expand(op.get('requestBody',{})).get('content',{})
    for media,value in content.items():
        branches=value.get('schema',{}).get('oneOf',[])
        if branches and len({json.dumps(x,sort_keys=True) for x in branches})<len(branches):
            issues.append({'kind':'duplicate_oneOf_alternatives','media':media,
                           'note':'Strict oneOf requires exactly one match; the client will not guess a replacement contract.'})
    return issues


UNSET = object()
DEFAULT_LIMIT = 16 * 1024 * 1024
HARD_LIMIT = 128 * 1024 * 1024
UUID_PATTERN = re.compile(r'[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z')
SAFE_METADATA = {'orchdVersion', 'getWordpressLatestVersion', 'getGlobalInstallableApps'}
ROOT_ONLY = {'setWebsiteBackupsDisabledStatus', 'getWebsiteDomainMapping'}


def canonical_key(key):
    return re.sub('[^a-z0-9]', '', key.lower())


def sensitive_key(key):
    key = canonical_key(key)
    return any(x in key for x in ('password','passwd','secret','token','credential','authorization','cookie','session','privatekey','apikey','accesskey','otp','passphrase')) or key in ('key','pin','auth','bearer')


def no_controls(value):
    if isinstance(value, str) and any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise CLIError('Control characters are forbidden in request parameters.')


def safe_segment(value):
    """Reject separators and traversal at every percent-decoding layer.

    Bound decoding work; fail closed on remaining percent escapes. Never
    normalize or repair a caller identifier. Encode the original exactly once.
    """
    original = str(value)
    if not original:
        raise CLIError('Empty path parameter is forbidden.')
    current = original
    for _ in range(16):
        no_controls(current)
        if current in ('.', '..') or any(x in current for x in ('/', '\\', '?', '#')):
            raise CLIError('Path traversal, separators, query or fragment injection rejected.')
        if re.search(r'%(?![0-9A-Fa-f]{2})', current):
            raise CLIError('Ambiguous percent encoding in path parameter.')
        try:
            decoded = urllib.parse.unquote(current, errors='strict')
        except UnicodeError:
            raise CLIError('Invalid UTF-8 percent encoding in path parameter.') from None
        if decoded == current:
            return urllib.parse.quote(original, safe='')
        current = decoded
    raise CLIError('Excessive encoded path chain rejected.')


def validate_base_url(value, allow_loopback=False):
    if not isinstance(value, str) or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in value) or any(c in value for c in ('\\','?','#')):
        raise CLIError('Invalid base_url: whitespace, query, fragment and backslash are forbidden.')
    try:
        u = urllib.parse.urlsplit(value)
        port = u.port
    except ValueError:
        raise CLIError('Invalid base_url authority or port.') from None
    if u.username is not None or u.password is not None or not u.hostname or '%' in u.netloc:
        raise CLIError('base_url requires a host, no userinfo or encoded authority.')
    loopback = False
    try:
        loopback = ipaddress.ip_address(u.hostname).is_loopback
    except ValueError:
        pass  # Only literal loopback IPs, never a DNS name, for tests.
    if u.scheme != 'https' and not (allow_loopback and u.scheme == 'http' and loopback):
        raise CLIError('HTTPS is required; only internal literal-loopback tests may use HTTP.')
    if port is not None and not 1 <= port <= 65535:
        raise CLIError('Invalid base_url port.')
    for segment in u.path.split('/'):
        if segment:
            safe_segment(segment)
    try:
        u.netloc.encode('ascii')
    except UnicodeError:
        raise CLIError('base_url host must be ASCII (use reviewed punycode if needed).') from None
    return value.rstrip('/')


def private_text(path):
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
    try:
        if Path(path).is_symlink():
            raise CLIError('Credential files must not be symbolic links.')
        fd = os.open(path, flags)
        with os.fdopen(fd, 'rb') as f:
            st = os.fstat(f.fileno())
            if not stat.S_ISREG(st.st_mode):
                raise CLIError('Credential file must be a regular file.')
            if os.name == 'posix' and (st.st_mode & 0o077 or st.st_uid != os.getuid()):
                raise CLIError('Credential file must be owned by you with no group/other permissions (chmod 600).')
            data = f.read(65537)
    except OSError:
        raise CLIError('Cannot securely read credential file.') from None
    if len(data) > 65536:
        raise CLIError('Credential file exceeds size limit.')
    try:
        return data.decode('utf-8')
    except UnicodeError:
        raise CLIError('Credential file must be UTF-8.') from None


class Profile:
    def __init__(self, data, config_dir=None, _allow_loopback=False):
        allowed = {'base_url','scope','org_id','website_id','domain_ids','server_id','server_context',
                   'token_env','token_file','token_env_file','token_env_key','name'}
        if not isinstance(data, dict) or set(data) - allowed:
            raise CLIError('Unknown profile fields; use the documented JSON profile format.')
        self.data = copy.deepcopy(data)
        self.base_url = validate_base_url(data.get('base_url'), _allow_loopback)
        self.scope = data.get('scope')
        if self.scope not in ('admin','organisation','site'):
            raise CLIError('scope must be admin, organisation or site.')
        for key in ('org_id','website_id','server_id'):
            if key in data and (not isinstance(data[key],str) or not UUID_PATTERN.fullmatch(data[key])):
                raise CLIError('Profile locator must be a literal UUID: ' + key)
        if self.scope in ('organisation','site') and not data.get('org_id'):
            raise CLIError('Restricted profiles require org_id.')
        if self.scope == 'site' and not data.get('website_id'):
            raise CLIError('Site profile requires website_id.')
        domains = data.get('domain_ids', [])
        if not isinstance(domains, list) or any(not isinstance(x,str) or not UUID_PATTERN.fullmatch(x) for x in domains) or len(set(domains)) != len(domains):
            raise CLIError('domain_ids must be an explicit, unique UUID array.')
        context = data.get('server_context', {})
        if not isinstance(context, dict) or any(not isinstance(k,str) or not isinstance(v,str) or not UUID_PATTERN.fullmatch(v) or 'server' not in canonical_key(k) for k,v in context.items()):
            raise CLIError('server_context must map exact server locator names to UUIDs.')
        sources = [k for k in ('token_env','token_file','token_env_file') if k in data]
        if len(sources) > 1:
            raise CLIError('Choose only one token_env, token_file or token_env_file source.')
        for key in sources:
            if not isinstance(data[key],str) or not data[key]:
                raise CLIError('Credential source must be a nonempty string.')
        for key in ('token_env','token_env_key'):
            if key in data and (not isinstance(data[key],str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', data[key])):
                raise CLIError('Credential environment key must be a valid variable name.')
        if ('token_env_file' in data) != ('token_env_key' in data):
            raise CLIError('token_env_file and token_env_key must be set together.')
        self.config_dir = Path(config_dir or '.')
        self.token = None

    def read_token(self):
        if self.token is not None:
            return self.token
        d = self.data
        if 'token_env' in d:
            token = os.environ.get(d['token_env'])
            if token is None:
                raise CLIError('Configured token environment variable is not set.', 'credentials')
        elif 'token_file' in d:
            token = private_text(self.config_dir / d['token_file']).rstrip('\r\n')
        elif 'token_env_file' in d:
            token = None
            for line in private_text(self.config_dir / d['token_env_file']).splitlines():
                line = line.strip()
                if line.startswith('export '):
                    line = line[7:].lstrip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                key, value = line.split('=',1)
                if key.strip() != d['token_env_key']:
                    continue
                if token is not None:
                    raise CLIError('Duplicate credential key in environment file.', 'credentials')
                value = value.strip()
                if len(value) >= 2 and value[0] in ('\"', "'") and value[-1] == value[0]:
                    value = value[1:-1]
                token = value  # Literal parsing only: no shell, interpolation or sourcing.
            if token is None:
                raise CLIError('Configured key is absent from token environment file.', 'credentials')
        else:
            return None
        if not isinstance(token,str) or not re.fullmatch(r'[A-Za-z0-9._~+/-]+=*', token) or len(token) > 8192:
            raise CLIError('Bearer token is empty or has an invalid length/character; no value printed.', 'credentials')
        self.token = token
        return token

    def bind(self, op, params):
        result = dict(params)
        for param in op['parameters']:
            key = param['name']
            norm = canonical_key(key)
            setting = {'orgid':'org_id','websiteid':'website_id','serverid':'server_id'}.get(norm)
            if setting and setting in self.data:
                if key in result and result[key] != self.data[setting]:
                    raise CLIError('Parameter conflicts with configured locator: ' + key, 'scope')
                result[key] = self.data[setting]
            if key in self.data.get('server_context', {}):
                if key in result and result[key] != self.data['server_context'][key]:
                    raise CLIError('Parameter conflicts with server context.', 'scope')
                result[key] = self.data['server_context'][key]
        return result

    def guard(self, op, params, body):
        if self.scope == 'admin':
            return
        path_keys = {canonical_key(p['name']) for p in op['parameters'] if p['in']=='path'}
        route = re.sub(r'^/v2(?=/)', '', op['path'])
        if op['operationId'] in ROOT_ONLY or re.match(r'^/(?:servers|settings|reports|backups|migrations|dns|licence|status|client_ip)(?:/|$)', route):
            raise CLIError('Cluster/root-only route is blocked in restricted profiles.', 'scope')
        if op['method']=='GET' and op['operationId'] in SAFE_METADATA and not params:
            return
        site_anchor = 'websiteid' in path_keys and bool(self.data.get('website_id'))
        domain_anchor = 'domainid' in path_keys and params.get('domain_id') in self.data.get('domain_ids', [])
        org_anchor = 'orgid' in path_keys
        if self.scope == 'site' and not (site_anchor or domain_anchor):
            raise CLIError('Site mode refuses org-wide or unanchored operations. Use a separately reviewed organisation profile.', 'scope')
        if self.scope == 'organisation' and not (org_anchor or site_anchor or domain_anchor):
            raise CLIError('Unanchored ownership cannot be proven from this profile; request blocked.', 'scope')
        scoped_params = dict(params)
        for param in op['parameters']:
            if param['in'] != 'path':
                continue
            norm = canonical_key(param['name'])
            if norm == 'domainid' and (site_anchor or (org_anchor and self.scope == 'organisation')):
                scoped_params.pop(param['name'], None)
            if norm == 'websiteid' and org_anchor and self.scope == 'organisation' and not self.data.get('website_id'):
                scoped_params.pop(param['name'], None)
        self._guard_values(scoped_params)
        if op['operationId'] == 'createWebsite' and 'kind' in params:
            raise CLIError('Special cluster website creation is blocked in restricted profiles.', 'scope')
        if body is not UNSET:
            self._guard_values(body)

    def _guard_values(self, obj):
        if isinstance(obj, dict):
            for key, value in obj.items():
                norm = canonical_key(key)
                vals = value if isinstance(value, list) else [value]
                if norm in ('org','orgs','organisation','organisations','organization','organizations','owner','owners',
                            'website','websites','site','sites','customer','customers','tenant','tenants',
                            'source','target','destination'):
                    raise CLIError('Ambiguous nested owner/site container rejected; use explicitly scoped schema locators.', 'scope')
                if ('org' in norm and (norm.endswith('id') or norm.endswith('ids'))) or norm in ('realmid','vendorid','subscriberid','ownerid'):
                    if any(x != self.data.get('org_id') for x in vals):
                        raise CLIError('Cross-organisation or ambiguous owner locator rejected.', 'scope')
                elif ('website' in norm or norm.startswith('site')) and (norm.endswith('id') or norm.endswith('ids')):
                    if not self.data.get('website_id') or any(x != self.data['website_id'] for x in vals):
                        raise CLIError('Website locator must match an explicit configured website_id.', 'scope')
                elif 'domain' in norm and (norm.endswith('id') or norm.endswith('ids')):
                    # A website-anchored route proves the path domain's containing site;
                    # extra body/query domain locators always need the explicit allowlist.
                    if any(x not in self.data.get('domain_ids', []) for x in vals):
                        raise CLIError('Domain locator must be in explicit domain_ids.', 'scope')
                elif 'server' in norm and (norm.endswith('id') or norm.endswith('ids') or norm == 'servers'):
                    allowed = set(self.data.get('server_context', {}).values())
                    if self.data.get('server_id'):
                        allowed.add(self.data['server_id'])
                    if any(x not in allowed for x in vals):
                        raise CLIError('Server locator requires explicit matching server context.', 'scope')
                elif norm=='showdeleted':
                    raise CLIError('The showDeleted parameter is master-only, including false; blocked in restricted profiles.', 'scope')
                elif norm in ('recursive','recursion') and value not in (False, None):
                    raise CLIError('Descendant recursion is blocked in restricted profiles.', 'scope')
                elif norm in ('customerid','tenantid','destinationid','targetid','sourceid'):
                    raise CLIError('Ambiguous extra owner/site locator rejected.', 'scope')
                self._guard_values(value)
        elif isinstance(obj,list):
            for item in obj:
                self._guard_values(item)


def validate(value, schema, catalog, where='input', depth=0):
    """Practical OpenAPI subset, not complete JSON Schema validation."""
    if depth > 60:
        raise CLIError('Input nesting exceeds validation limit.')
    schema = catalog.resolve(schema)
    if value is None:
        if schema.get('nullable'):
            return
        if schema.get('type') or 'enum' in schema:
            raise CLIError(where + ': null is not allowed.')
    for subschema in schema.get('allOf', []):
        validate(value, subschema, catalog, where, depth+1)
    for union in ('oneOf','anyOf'):
        if union in schema:
            matches = 0
            for branch in schema[union]:
                try:
                    validate(value, branch, catalog, where, depth+1)
                    matches += 1
                except CLIError:
                    pass
            if union=='oneOf' and matches>1:
                raise CLIError(where + ': multiple oneOf alternatives match. Ambiguous schema/input; no branch is guessed.', 'unsupported')
            if (union == 'oneOf' and matches != 1) or (union=='anyOf' and not matches):
                raise CLIError(where + ': does not match schema ' + union + '.')
    kind = schema.get('type')
    checks = {'object': lambda: isinstance(value,dict), 'array': lambda: isinstance(value,list),
              'string': lambda: isinstance(value,str), 'boolean': lambda: type(value) is bool,
              'integer': lambda: type(value) is int,
              'number': lambda: type(value) is int or (type(value) is float and math.isfinite(value))}
    if kind and (kind not in checks or not checks[kind]()):
        raise CLIError(where + ': expected ' + str(kind) + '.')
    if 'enum' in schema and not any(type(value) is type(x) and value == x for x in schema['enum']):
        raise CLIError(where + ': value is not in the described enum.')
    if isinstance(value,str):
        if schema.get('format')=='uuid' and not UUID_PATTERN.fullmatch(value):
            raise CLIError(where + ': expected a literal UUID, without normalization.')
        if len(value) < schema.get('minLength',0) or ('maxLength' in schema and len(value)>schema['maxLength']):
            raise CLIError(where + ': string length outside bounds.')
    if type(value) in (int,float):
        if type(value) is float and not math.isfinite(value):
            raise CLIError(where + ': non-finite number rejected.')
        for bound, direction in (('minimum',-1),('maximum',1)):
            if bound in schema and ((value-schema[bound])*direction > 0 or (value==schema[bound] and schema.get('exclusive'+bound.title()))):
                raise CLIError(where + ': number outside bounds.')
    if isinstance(value,list):
        if len(value)<schema.get('minItems',0) or ('maxItems' in schema and len(value)>schema['maxItems']):
            raise CLIError(where + ': array length outside bounds.')
        if schema.get('uniqueItems') and len({json.dumps(x,sort_keys=True) for x in value}) != len(value):
            raise CLIError(where + ': duplicate array items rejected.')
        for item in value:
            validate(item, schema.get('items',{}), catalog, where+'[]', depth+1)
    if isinstance(value,dict):
        props = schema.get('properties', {})
        if any(k not in value for k in schema.get('required', [])):
            # Schema-authored names only, never caller keys or values in errors.
            missing = [k for k in schema.get('required', []) if k not in value]
            raise CLIError(where + ': required fields absent: ' + ', '.join(missing))
        extras = set(value)-set(props)
        if extras and schema.get('additionalProperties') is False:
            raise CLIError(where + ': unknown fields in closed object.')
        for key, item in value.items():
            if key in props:
                validate(item, props[key], catalog, where+'.'+key, depth+1)
            elif isinstance(schema.get('additionalProperties'),dict):
                validate(item, schema['additionalProperties'], catalog, where+'.<additional>', depth+1)


def query_pairs(param, value):
    name = param['name']
    style = param.get('style', 'form')
    explode = param.get('explode', style=='form')
    if isinstance(value,dict) or style not in ('form','spaceDelimited','pipeDelimited'):
        raise CLIError('Unsupported query serialization style; see describe.', 'unsupported')
    scalar = lambda x: str(x).lower() if type(x) is bool else str(x)
    values = value if isinstance(value,list) else [value]
    for item in values:
        no_controls(item)
    if isinstance(value,list):
        if style == 'form' and explode:
            return [(name,scalar(x)) for x in values] if values else [(name,'')]
        separator = {'form':',','spaceDelimited':' ','pipeDelimited':'|'}[style]
        return [(name, separator.join(scalar(x) for x in values))]
    if style!='form':
        raise CLIError('Delimited style requires a query array.', 'unsupported')
    return [(name,scalar(value))]


@dataclass(repr=False)
class Prepared:
    operation: dict
    method: str
    url: str
    params: dict
    headers: dict
    body: object
    guidance: dict
    auth: str

    def summary(self):
        return {'operationId': self.operation['operationId'], 'method': self.method,
                'route_template': self.operation['path'], 'parameter_names': sorted(self.params),
                'body_bytes': len(self.body) if self.body is not None else 0,
                'content_type': self.headers.get('Content-Type'), 'auth_support': self.auth,
                'guidance': self.guidance}


def build_multipart(catalog, media, fields, files, limit):
    schema = catalog.resolve(media.get('schema', {}))
    props = schema.get('properties', {})
    if not isinstance(fields,dict) or not isinstance(files,dict) or set(fields)&set(files) or (set(fields)|set(files))-set(props):
        raise CLIError('Multipart fields/files must match distinct described properties.')
    values = dict(fields, **{k:'<binary>' for k in files})
    validate(values, schema, catalog, 'multipart')
    boundary = 'enhance-' + secrets.token_hex(16)
    parts = []
    size = 0
    for key in list(fields) + list(files):
        if not re.fullmatch(r'[A-Za-z0-9_.-]+',key):
            raise CLIError('Unsafe multipart property name.', 'unsupported')
        prop = catalog.resolve(props[key])
        encoding = media.get('encoding',{}).get(key,{})
        if any(x in encoding for x in ('headers','style','explode','allowReserved')):
            raise CLIError('Unsupported multipart encoding; see describe.', 'unsupported')
        header = 'Content-Disposition: form-data; name="' + key + '"'
        if key in files:
            if prop.get('format')!='binary' or prop.get('type')!='string':
                raise CLIError('File supplied to a nonbinary multipart property.')
            path = Path(files[key])
            # Never interpolate the original filename; preserve only a safe extension.
            ext = path.suffix.lower()
            if not re.fullmatch(r'\.[a-z0-9]{1,12}',ext):
                ext = ''
            payload = read_bytes(path,limit)
            header += '; filename="upload' + ext + '"'
            content_type = mimetypes.guess_type('upload'+ext)[0] or 'application/octet-stream'
            permitted = [x.strip() for x in encoding.get('contentType','').split(',') if x.strip()]
            if permitted and content_type not in permitted:
                # .ico has platform-dependent MIME guesses; choose only a schema-approved MIME.
                if ext=='.ico' and 'image/ico' in permitted:
                    content_type='image/ico'
                else:
                    raise CLIError('File MIME type is outside the declared multipart encoding.')
        else:
            if prop.get('format')=='binary':
                raise CLIError('Binary multipart property requires --file.')
            if isinstance(fields[key],(dict,list)):
                payload = json.dumps(fields[key],ensure_ascii=True,allow_nan=False).encode('utf-8')
                content_type='application/json'
            else:
                value=fields[key]
                payload=(str(value).lower() if isinstance(value,bool) else str(value)).encode('utf-8')
                content_type='text/plain; charset=utf-8'
        if key not in files and encoding.get('contentType'):
            permitted={x.strip().split(';',1)[0].lower() for x in encoding['contentType'].split(',')}
            if content_type.split(';',1)[0].lower() not in permitted:
                raise CLIError('Unsupported nonbinary multipart content encoding.', 'unsupported')
        part = ('--'+boundary+'\r\n'+header+'\r\nContent-Type: '+content_type+'\r\n\r\n').encode('ascii') + payload + b'\r\n'
        size += len(part)
        if size > limit:
            raise CLIError('Multipart request exceeds size limit.')
        parts.append(part)
    result = b''.join(parts) + ('--'+boundary+'--\r\n').encode('ascii')
    if len(result)>limit:
        raise CLIError('Multipart request exceeds size limit.')
    return result, 'multipart/form-data; boundary='+boundary


def prepare(catalog, profile, operation, params=None, body=UNSET, files=None, raw_file=None, max_bytes=DEFAULT_LIMIT):
    if params is not None and not isinstance(params,dict):
        raise CLIError('params must be an object.')
    if type(max_bytes) is not int or not 1 <= max_bytes <= HARD_LIMIT:
        raise CLIError('max_bytes must be a positive integer within the hard limit.')
    op = catalog.operation(operation)
    auth = auth_support(op)
    if auth.startswith('unsupported'):
        raise CLIError('Operation requires unsupported authentication (sessionCookie/other scheme); bearer client cannot execute it.', 'unsupported')
    params = profile.bind(op, params or {})
    definitions = {p['name']:p for p in op['parameters']}
    if len(definitions)!=len(op['parameters']):
        raise CLIError('Ambiguous same-name parameters in different locations are unsupported.', 'unsupported')
    if set(params)-set(definitions):
        raise CLIError('Unrecognized input parameters. Names are exact; use describe.')
    headers = {'Accept':'application/json', 'Accept-Encoding':'identity', 'User-Agent':'enhance-stdlib-cli/1'}
    pairs = []
    path = op['path']
    for name, param in definitions.items():
        if name.lower()=='authorization' and param['in']=='header':
            if name in params:
                raise CLIError('Authorization is managed only by the configured credential source.')
            continue
        if name not in params:
            if param.get('required') or param['in']=='path':
                raise CLIError('Required parameter absent: '+name)
            continue
        value = params[name]
        if 'schema' not in param or 'content' in param:
            raise CLIError('Parameter content encoding is unsupported.', 'unsupported')
        validate(value, param['schema'], catalog, name)
        if param['in']=='path':
            if isinstance(value,(dict,list)) or param.get('style','simple')!='simple':
                raise CLIError('Complex/non-simple path parameter is unsupported.', 'unsupported')
            path = path.replace('{'+name+'}',safe_segment(str(value).lower() if type(value) is bool else value))
        elif param['in']=='query':
            pairs.extend(query_pairs(param,value))
        elif param['in']=='header':
            if name.lower() in ('host','cookie','content-type','content-length','transfer-encoding','connection','proxy-authorization'):
                raise CLIError('Reserved transport header cannot be supplied.', 'unsupported')
            if isinstance(value,(dict,list)) or not re.fullmatch(r'[A-Za-z0-9-]+', name):
                raise CLIError('Complex/invalid header parameters are unsupported.', 'unsupported')
            no_controls(value)
            try:
                str(value).encode('ascii')
            except UnicodeError:
                raise CLIError('Header parameters must be ASCII.') from None
            headers[name] = str(value)
        else:
            raise CLIError('Cookie parameter transport is unsupported.', 'unsupported')
    if '{' in path or '}' in path:
        raise CLIError('Schema route contains an undescribed path parameter.', 'unsupported')
    # Do not allow even schema-authored traversal in a candidate.
    for segment in path.split('/'):
        if segment:
            safe_segment(segment)
    profile.guard(op,params,body)
    request_body = catalog.resolve(op.get('requestBody',{}))
    content = request_body.get('content', {})
    supplied = body is not UNSET or files is not None or raw_file is not None
    if supplied and not content:
        raise CLIError('Operation does not describe a request body.')
    if request_body.get('required') and not supplied:
        raise CLIError('Required request body absent.')
    payload = None
    if supplied:
        if raw_file is not None:
            if body is not UNSET or files is not None or 'application/gzip' not in content:
                raise CLIError('Raw upload requires application/gzip and no other body inputs.')
            payload = read_bytes(raw_file,max_bytes)
            if not payload.startswith(b'\x1f\x8b'):
                raise CLIError('Raw gzip upload lacks a gzip signature (archive contents not validated).')
            headers['Content-Type']='application/gzip'
        elif files is not None or ('multipart/form-data' in content and 'application/json' not in content):
            if 'multipart/form-data' not in content:
                raise CLIError('Operation does not accept multipart files.')
            payload, headers['Content-Type'] = build_multipart(catalog,content['multipart/form-data'],{} if body is UNSET else body,files or {},max_bytes)
        elif 'application/json' in content:
            validate(body,content['application/json'].get('schema',{}),catalog,'body')
            try:
                payload=json.dumps(body,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode('utf-8')
            except (ValueError,TypeError,RecursionError):
                raise CLIError('Body cannot be encoded as finite JSON.') from None
            headers['Content-Type']='application/json'
        else:
            raise CLIError('Unsupported request media type; see describe.', 'unsupported')
    if payload is not None and len(payload)>max_bytes:
        raise CLIError('Request body exceeds size limit.')
    info=guidance(op,catalog)
    if any(params.get(x) is True for x in ('flush','refreshCache','shouldRedirect')):
        info['requires_apply']=True
        info['reason']='Request explicitly enables flushing, refresh or redirect behavior.'
    url = profile.base_url + path
    if pairs:
        # Encode all reserved data even if allowReserved=true: values stay data.
        url += '?' + urllib.parse.urlencode(pairs)
    return Prepared(op,op['method'],url,params,headers,payload,info,auth)


def redact(value, token=None, depth=0):
    if depth > 60:
        return '[REDACTED: nesting limit]'
    if isinstance(value,dict):
        named_secret = any(isinstance(value.get(k),str) and sensitive_key(value[k]) for k in ('name','key','option','setting'))
        result={}
        for key,item in value.items():
            clean_key=replace_token(key,token)
            result[clean_key]='[REDACTED]' if sensitive_key(key) or (named_secret and key in ('value','data','content')) else redact(item,token,depth+1)
        return result
    if isinstance(value,list):
        return [redact(x,token,depth+1) for x in value]
    if isinstance(value,str):
        value=replace_token(value,token)
        if re.search(r'(?i)(?:bearer\s+|-----BEGIN .*PRIVATE KEY|(?:password|passwd|secret|token|api[_-]?key|authorization|cookie|session|credential)\s*[=:]|https?://[^\s/]+@)',value):
            return '[REDACTED: sensitive text]'
        # JSON serialized inside JSON is common in upstream diagnostics.
        if value.lstrip().startswith(('{','[')):
            try:
                parsed=parse_json(value)
                return json.dumps(redact(parsed,token,depth+1),separators=(',',':'))
            except CLIError:
                if re.search(r'(?i)password|secret|token|credential|private.?key',value):
                    return '[REDACTED: sensitive text]'
        return value
    return value


def replace_token(text, token):
    if token:
        for variant in {token,urllib.parse.quote(token,safe=''),urllib.parse.quote_plus(token,safe='')}:
            text=text.replace(variant,'[REDACTED]')
    return text


def status_error(status):
    guidance_by_status={401:'Authentication rejected. Check the configured token source and the operation security requirements.',
                        403:'Authentication or authorization rejected. For organisation API tokens, first check the complete token-id_secret bearer format (not the secret alone), then principal roles and org/site grants; do not widen the token automatically.',
                        404:'Resource/route not found. Check exact IDs, API base prefix and schema version; permission masking is also possible.',
                        429:'Rate limited. Wait and retry manually according to server policy; no automatic retry is performed.'}
    return CLIError('HTTP '+str(status)+': '+guidance_by_status.get(status,'Request failed. Inspect server-side diagnostics through an approved private channel; response body withheld.'),'http',status)


def validate_limits(timeout,max_bytes):
    if type(timeout) not in (int,float) or not math.isfinite(timeout) or not 0 < timeout <= 300:
        raise CLIError('Timeout must be finite and within (0, 300] seconds.')
    if type(max_bytes) is not int or not 1 <= max_bytes <= HARD_LIMIT:
        raise CLIError('Response size limit is outside allowed bounds.')


def transport(request, profile, timeout=30.0, max_bytes=DEFAULT_LIMIT, _received=None):
    validate_limits(timeout,max_bytes)
    url=urllib.parse.urlsplit(request.url)
    headers=dict(request.headers)
    if not request.auth.startswith('public'):
        token=profile.read_token()
        needs_header=any(p['in']=='header' and p['name'].lower()=='authorization' and p.get('required') for p in request.operation['parameters'])
        if not token and (request.auth=='bearer' or needs_header):
            raise CLIError('This operation requires a configured bearer credential source.', 'credentials')
        if token:
            headers['Authorization']='Bearer '+token
    # No cookie jar, proxy inheritance, redirect handling, automatic retries or SSL bypass.
    if url.scheme=='https':
        conn=http.client.HTTPSConnection(url.hostname,url.port,timeout=timeout,context=ssl.create_default_context())
    else:
        conn=http.client.HTTPConnection(url.hostname,url.port,timeout=timeout)
    started=time.monotonic()
    try:
        conn.request(request.method,url.path+('?' + url.query if url.query else ''),body=request.body,headers=headers)
        resp=conn.getresponse()
        status=resp.status
        if 300 <= status < 400:
            raise CLIError('Redirect refused, including same-host redirects. No Location URL is printed or followed.', 'redirect', status)
        if not 200 <= status < 300:
            raise status_error(status)
        if resp.getheader('Content-Encoding','identity').lower() not in ('','identity'):
            raise CLIError('Unexpected compressed response encoding; decompression is not performed.', 'unsupported')
        length=resp.getheader('Content-Length')
        if length is not None:
            if not re.fullmatch(r'[0-9]+',length):
                raise CLIError('Invalid response Content-Length.', 'transport')
            if int(length)>max_bytes:
                raise CLIError('Response exceeds configured size limit.', 'size')
        chunks=[]
        size=0
        while True:
            remaining=timeout-(time.monotonic()-started)
            if remaining<=0:
                raise CLIError('Request deadline exceeded; no retry was attempted.', 'timeout')
            if conn.sock:
                conn.sock.settimeout(remaining)
            chunk=resp.read1(min(65536,max_bytes-size+1))
            if not chunk:
                break
            size+=len(chunk)
            if _received is not None:
                _received[0]+=len(chunk)
            if size>max_bytes:
                raise CLIError('Response exceeds configured size limit.', 'size')
            chunks.append(chunk)
        if length is not None and size != int(length):
            raise CLIError('Incomplete response body.', 'transport')
        return status, resp.getheader('Content-Type','').split(';',1)[0].strip().lower(), b''.join(chunks)
    except ssl.SSLError:
        raise CLIError('TLS verification/handshake failed. Fix the certificate or trust chain; TLS verification cannot be disabled.', 'tls') from None
    except (socket.timeout,TimeoutError):
        raise CLIError('Network timeout; no retry was attempted. Outcome of a mutation may be unknown.', 'timeout') from None
    except (OSError,http.client.HTTPException,ValueError,UnicodeError):
        raise CLIError('Transport failed; request/response values withheld. No retry was attempted; mutation outcome may be unknown.', 'transport') from None
    finally:
        conn.close()


def reserve_output(path):
    try:
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,'O_NOFOLLOW',0),0o600)
        if os.name=='posix':
            os.fchmod(fd,0o600)
        return os.fdopen(fd,'wb')
    except OSError:
        raise CLIError('Output must be a NEW writable file; existing files and symlinks are never overwritten.', 'output') from None


def pointer(value, path):
    if path=='':
        return value
    for piece in path[1:].split('/'):
        key=piece.replace('~1','/').replace('~0','~')
        try:
            if isinstance(value,list) and re.fullmatch(r'0|[1-9][0-9]*',key):
                value=value[int(key)]
            elif isinstance(value,dict):
                value=value[key]
            else:
                raise KeyError()
        except (KeyError,IndexError):
            raise CLIError('Client-side JSON pointer does not resolve; data was not silently discarded.', 'view') from None
    return value


def validate_view(view):
    if view is None:
        return {}
    if not isinstance(view,dict) or set(view)-{'items','filter','select','count'}:
        raise CLIError('Unknown client-side view fields.')
    paths=[]
    if 'items' in view:
        paths.append(view['items'])
    if 'filter' in view:
        if not isinstance(view['filter'],dict):
            raise CLIError('view.filter must be a JSON-pointer-to-value object.')
        paths.extend(view['filter'])
    if 'select' in view:
        if not isinstance(view['select'],list) or not view['select']:
            raise CLIError('view.select must be a nonempty JSON pointer array.')
        paths.extend(view['select'])
    if 'count' in view and type(view['count']) is not bool:
        raise CLIError('view.count must be boolean.')
    for path in paths:
        if not isinstance(path,str) or (path and not path.startswith('/')) or re.search(r'~(?![01])',path):
            raise CLIError('Client-side fields use RFC 6901 JSON pointers, e.g. /id.')
        no_controls(path)
    return view


def project(data, view):
    view=validate_view(view)
    if not view:
        return data
    item_path=view.get('items','/items' if isinstance(data,dict) and isinstance(data.get('items'),list) else '')
    items=pointer(data,item_path)
    if not isinstance(items,list):
        raise CLIError('Count/filter/projection requires an explicit or conventional items array.', 'view')
    selected=[]
    for item in items:
        match=True
        for path,expected in view.get('filter',{}).items():
            actual=pointer(item,path)
            if type(actual) is not type(expected) or actual!=expected:
                match=False
                break
        if match:
            selected.append(item)
    result={'returned_count':len(items),'matched_count':len(selected)}
    if item_path:
        # Preserve ALL outer response metadata, including nested totals. Replace
        # only the chosen array with an explicit marker rather than duplicating it.
        metadata=copy.deepcopy(data)
        pieces=item_path[1:].split('/')
        parent=metadata
        for piece in pieces[:-1]:
            key=piece.replace('~1','/').replace('~0','~')
            parent=parent[int(key)] if isinstance(parent,list) else parent[key]
        key=pieces[-1].replace('~1','/').replace('~0','~')
        parent[int(key) if isinstance(parent,list) else key]='[selected array reported separately]'
        result['response_metadata']=metadata
    if not view.get('count'):
        result['items']=[{path:pointer(item,path) for path in view['select']} for item in selected] if 'select' in view else selected
    return result


def execute(request, profile, apply=False, output=None, timeout=30.0, max_bytes=DEFAULT_LIMIT, view=None, dry_run=False, _received=None):
    validate_limits(timeout,max_bytes)
    view=validate_view(view)
    if output and view:
        raise CLIError('--output preserves raw response bytes and cannot be combined with client-side views.')
    if dry_run or (request.guidance['requires_apply'] and not apply):
        return dict(request.summary(), dry_run=True, sent=False,
                    next_step='Review the described schema and scope. Explicit --apply is required for mutations/sensitive reads.')
    if request.guidance['requires_output'] and output is None:
        raise CLIError('This sensitive/log/download operation requires a new --output file as well as --apply.', 'output')
    # Reserve before any request; otherwise a mutation might succeed before a
    # path-exists error. The file remains empty on any transport failure.
    sink=reserve_output(output) if output is not None else None
    try:
        status,media,raw=transport(request,profile,timeout,max_bytes,_received=_received)
        result={'operationId':request.operation['operationId'],'status':status,'response_bytes':len(raw)}
        if sink is not None:
            sink.write(raw)
            sink.flush()
            os.fsync(sink.fileno())
            return dict(result,output_written=True,raw_sensitive_output=True)
        if not raw:
            data=None
        elif request.operation['operationId']=='orchdVersion':
            try:
                text=raw.decode('utf-8').strip()
            except UnicodeError:
                raise CLIError('Version response is not UTF-8.', 'response') from None
            try:
                data=parse_json(text)
            except CLIError:
                data=text
            if not isinstance(data,str) or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?',data):
                raise CLIError('Unexpected version response; content withheld.', 'response')
        elif media=='application/json' or media.endswith('+json'):
            try:
                data=parse_json(raw, allow_identical_duplicates=True)
            except CLIError:
                raise CLIError('Successful response was not valid JSON; use explicit --output to retain bytes privately.', 'response') from None
            if isinstance(data,str) and request.operation['operationId'] not in ('orchdStatus','getClientIp','getWordpressLatestVersion'):
                raise CLIError('Unstructured JSON text requires a new --output file; content is not printed.', 'output')
        else:
            raise CLIError('Non-JSON response requires a new --output file; bytes are not printed.', 'output')
        result['data']=redact(project(redact(data,profile.token),view),profile.token)
        return result
    except OSError:
        raise CLIError('Private output write failed; request may have succeeded. Do not blindly retry a mutation.', 'output') from None
    finally:
        if sink is not None:
            sink.close()


def error_record(err):
    result={'code':err.code,'message':str(err)}
    if err.status is not None:
        result['status']=err.status
    return result


def batch(catalog, profile, plan, timeout=30.0, max_bytes=DEFAULT_LIMIT):
    validate_limits(timeout,max_bytes)
    if not isinstance(plan,list) or not 1<=len(plan)<=50:
        raise CLIError('Batch must be an explicit JSON array of 1 to 50 reads.')
    prepared=[]
    for entry in plan:
        if not isinstance(entry,dict) or set(entry)-{'operationId','params','view'} or 'operationId' not in entry:
            raise CLIError('Batch entry allows only operationId, params and view; no writes/body/output/apply.')
        if entry['operationId'] not in catalog.operations:
            raise CLIError('Batch entries require exact operationId, not aliases or URLs.')
        view=validate_view(entry.get('view'))
        request=prepare(catalog,profile,entry['operationId'],entry.get('params'),max_bytes=max_bytes)
        if request.method!='GET' or request.guidance['requires_apply'] or request.guidance['requires_output']:
            raise CLIError('Batch accepts only ordinary GET reads; mutations/sensitive/side-effect GETs are forbidden.')
        prepared.append((request,view))
    # Resolve credential prerequisites once, after EVERY request was preflighted.
    if any(not req.auth.startswith('public') for req,_ in prepared):
        token=profile.read_token()
        if not token and any(req.auth=='bearer' or any(p['in']=='header' and p['name'].lower()=='authorization' and p.get('required') for p in req.operation['parameters']) for req,_ in prepared):
            raise CLIError('Batch includes an operation requiring a configured bearer source.', 'credentials')
    results=[]
    succeeded=0
    budget=max_bytes
    for request,view in prepared:
        received=[0]
        try:
            if budget<=0:
                raise CLIError('Aggregate batch response byte budget exhausted; this operation was not sent.', 'size')
            result=execute(request,profile,timeout=timeout,max_bytes=budget,view=view,_received=received)
            results.append(dict(result,ok=True))
            succeeded+=1
        except CLIError as err:
            results.append({'operationId':request.operation['operationId'],'ok':False,'error':error_record(err)})
            if err.code=='size':
                budget=0
        finally:
            budget=max(0,budget-received[0])
    return {'requested':len(prepared),'succeeded':succeeded,'failed':len(prepared)-succeeded,
            'pagination':'none; each explicit plan entry attempted at most once','results':results}


def refresh_status(catalog=None, freshness_path=None, today=None):
    catalog=catalog or Catalog()
    info=load_json(freshness_path or ROOT/'assets/freshness.json')
    try:
        retrieved=datetime.date.fromisoformat(info['retrieved_date'])
    except (ValueError,KeyError,TypeError):
        raise CLIError('Invalid bundled retrieved_date; freshness cannot be computed.') from None
    today=today or datetime.datetime.now(datetime.timezone.utc).date()
    age=(today-retrieved).days
    return dict(info,active_schema_version=catalog.version,age_days=age,
                review_due=age>=30, future_date=age<0,
                version_metadata_matches=info.get('schema_version')==catalog.version,
                policy='No network update, cron, active-schema write or automatic permission change. Review a separately fetched candidate with schema-diff.')


def schema_diff(active, candidate):
    old,new=active.operations,candidate.operations
    added=sorted(set(new)-set(old))
    removed=sorted(set(old)-set(new))
    changed=[]
    fields=('method','path','parameters','requestBody','responses','security','security_schemes','summary','description','tags','deprecated')
    for name in sorted(set(old)&set(new)):
        before,after=active.expand(old[name]),candidate.expand(new[name])
        differences=[key for key in fields if before.get(key)!=after.get(key)]
        if differences:
            row={'operationId':name,'changed_fields':differences}
            if 'parameters' in differences:
                a={(x['in'],x['name']):x for x in before['parameters']}
                b={(x['in'],x['name']):x for x in after['parameters']}
                label=lambda xs: [place+':'+key for place,key in sorted(xs)]
                row['parameters']={'added':label(set(b)-set(a)),'removed':label(set(a)-set(b)),
                                   'changed':label(k for k in set(a)&set(b) if a[k]!=b[k])}
            if 'security' in differences:
                row['security']={'before':before.get('security'),'after':after.get('security')}
            if 'requestBody' in differences:
                row['request_media']={'before':sorted(before.get('requestBody',{}).get('content',{})),
                                      'after':sorted(after.get('requestBody',{}).get('content',{}))}
            changed.append(row)
    return {'active_version':active.version,'candidate_version':candidate.version,
            'active_count':len(old),'candidate_count':len(new),'added_count':len(added),'removed_count':len(removed),'changed_count':len(changed),
            'added':added,'removed':removed,'changed':changed,
            'policy':'Comparison only. Active files and permissions were not changed. Review semantic changes before adoption.'}


class SafeParser(argparse.ArgumentParser):
    def error(self,message):
        raise CLIError('Invalid CLI arguments. Use --help. Argument values are never echoed.')


def parse_assignment(text):
    if '=' not in text:
        raise CLIError('Expected name=value; input values are not echoed.')
    key,value=text.split('=',1)
    if not key:
        raise CLIError('Empty input name.')
    no_controls(key)
    return key,value


def cli_params(catalog, operation, entries, file_path):
    params=load_json(file_path,DEFAULT_LIMIT) if file_path else {}
    if not isinstance(params,dict):
        raise CLIError('Parameter file must be a JSON object.')
    definitions={x['name']:x for x in catalog.operation(operation)['parameters']}
    for entry in entries:
        key,text=parse_assignment(entry)
        if key not in definitions:
            raise CLIError('Unknown parameter name. Use describe; names are case-sensitive.')
        if key in params:
            raise CLIError('Duplicate parameter supplied.')
        if sensitive_key(key):
            raise CLIError('Sensitive parameters must come from --params-file, never command arguments. Bearer tokens use profile sources only.')
        schema=catalog.resolve(definitions[key].get('schema',{}))
        if schema.get('type')=='string':
            params[key]=text
        else:
            params[key]=parse_json(text)
    return params


def parser():
    p=SafeParser(description=__doc__)
    p.add_argument('--config',help='JSON profile file (no credential values)')
    p.add_argument('--profile',help='Named profile inside a profiles object')
    p.add_argument('--pretty',action='store_true',help='Indent output JSON')
    p.add_argument('--_allow-loopback-http',action='store_true',help=argparse.SUPPRESS)
    sub=p.add_subparsers(dest='command',required=True)
    ops=sub.add_parser('ops',help='Discover ALL operations, counts and truthful aliases')
    ops.add_argument('--search')
    ops.add_argument('--tag')
    ops.add_argument('--method',choices=[x.upper() for x in METHODS])
    desc=sub.add_parser('describe',help='Resolved schema, exact inputs, security and side effects')
    desc.add_argument('operation')
    call=sub.add_parser('call',help='Read or locally dry-run an operationId')
    call.add_argument('operation')
    call.add_argument('--param',action='append',default=[],metavar='NAME=VALUE')
    call.add_argument('--params-file',help='Typed JSON parameter object; use for sensitive parameters')
    call.add_argument('--body-file',help='JSON request body, or multipart non-file field object')
    call.add_argument('--file',action='append',default=[],metavar='FIELD=PATH',help='Multipart binary field')
    call.add_argument('--raw-file',help='Raw application/gzip body')
    call.add_argument('--apply',action='store_true')
    call.add_argument('--dry-run',action='store_true',help='Force local construction even for ordinary GET')
    call.add_argument('--output',help='NEW mode-0600 file; raw response, potentially secret')
    call.add_argument('--items',help='JSON pointer selecting the response array; default /items or root array')
    call.add_argument('--where',action='append',default=[],metavar='POINTER=JSON',help='Client-side exact equality filter')
    call.add_argument('--select',action='append',default=[],metavar='POINTER',help='Client-side projection; repeat')
    call.add_argument('--count',action='store_true',help='Client-side counts; retain ALL response metadata/totals')
    b=sub.add_parser('batch',help='Preflight ALL then run an explicit JSON array of ordinary GETs')
    b.add_argument('plan')
    for cmd in (call,b):
        cmd.add_argument('--timeout',type=float,default=30.0)
        cmd.add_argument('--max-bytes',type=int,default=DEFAULT_LIMIT,help='Request/response bound; batch aggregate response budget; hard cap 128 MiB')
    sub.add_parser('refresh-status',help='Offline date-only freshness diagnostics')
    diff=sub.add_parser('schema-diff',help='Compare a reviewed candidate without changing active files')
    diff.add_argument('candidate')
    return p


def load_profile(path, name=None, allow_loopback=False):
    if not path:
        raise CLIError('call/batch requires --config with an explicitly reviewed profile.')
    data=load_json(path,DEFAULT_LIMIT)
    if isinstance(data,dict) and 'profiles' in data:
        if set(data)-{'profiles','default_profile'} or not isinstance(data['profiles'],dict):
            raise CLIError('Invalid profiles document.')
        selected=name or data.get('default_profile')
        if selected not in data['profiles']:
            raise CLIError('Select an existing profile with --profile or default_profile.')
        data=data['profiles'][selected]
    elif name:
        raise CLIError('--profile requires a profiles document, not a single profile.')
    return Profile(data,Path(path).resolve().parent,_allow_loopback=allow_loopback)


def main(argv=None):
    profile=None
    try:
        args=parser().parse_args(argv)
        catalog=Catalog()
        freshness=refresh_status(catalog)
        if args.command!='refresh-status' and (freshness['review_due'] or freshness['future_date'] or not freshness['version_metadata_matches']):
            print('Advisory: bundled schema freshness needs review. Run refresh-status and compare a separately fetched candidate; no automatic update.',file=sys.stderr)
        if args.command=='ops':
            result=catalog.list_ops(args.search,args.tag,args.method)
        elif args.command=='describe':
            result=catalog.describe(args.operation)
        elif args.command=='refresh-status':
            result=freshness
        elif args.command=='schema-diff':
            result=schema_diff(catalog,Catalog(args.candidate))
        else:
            validate_limits(args.timeout,args.max_bytes)
            profile=load_profile(args.config,args.profile,args._allow_loopback_http)
            if args.command=='batch':
                result=batch(catalog,profile,load_json(args.plan,DEFAULT_LIMIT),args.timeout,args.max_bytes)
            else:
                params=cli_params(catalog,args.operation,args.param,args.params_file)
                files={}
                for entry in args.file:
                    key,path=parse_assignment(entry)
                    if key in files:
                        raise CLIError('Duplicate multipart field.')
                    files[key]=path
                body=load_json(args.body_file,args.max_bytes) if args.body_file else UNSET
                request=prepare(catalog,profile,args.operation,params,body,files or None,args.raw_file,args.max_bytes)
                view={}
                if args.items is not None:
                    view['items']=args.items
                if args.count:
                    view['count']=True
                if args.select:
                    view['select']=args.select
                if args.where:
                    view['filter']={}
                    for entry in args.where:
                        key,value=parse_assignment(entry)
                        if key in view['filter']:
                            raise CLIError('Duplicate client-side filter.')
                        view['filter'][key]=parse_json(value)
                result=execute(request,profile,args.apply,args.output,args.timeout,args.max_bytes,view,args.dry_run)
        text=json.dumps(result,ensure_ascii=True,allow_nan=False,indent=2 if args.pretty else None,separators=None if args.pretty else (',',':'))
        print(replace_token(text,profile.token if profile else None))
        return 1 if args.command=='batch' and result['failed'] else 0
    except CLIError as err:
        print(replace_token(json.dumps({'error':error_record(err)},ensure_ascii=True,separators=(',',':')),profile.token if profile else None),file=sys.stderr)
        return 2
    except (OSError,ValueError,TypeError,KeyError,UnicodeError,RecursionError):
        print('{"error":{"code":"invalid_input","message":"Invalid local input or unexpected response structure; values withheld."}}',file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print('{"error":{"code":"interrupted","message":"Interrupted; mutation outcome may be unknown. Do not blindly retry."}}',file=sys.stderr)
        return 130


if __name__=='__main__':
    sys.exit(main())
