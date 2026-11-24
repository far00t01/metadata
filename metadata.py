#!/usr/bin/env python3
import argparse
import gzip
import io
import itertools
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import warnings
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter, defaultdict, deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from urllib.parse import parse_qsl, unquote, urldefrag, urljoin, urlparse, urlunparse

warnings.filterwarnings("ignore", module="bs4")
warnings.filterwarnings("ignore", message=".*Some characters could not be decoded.*")

VERSION = "2.0"


def check_environment_and_dependencies():
    in_venv = (hasattr(sys, "real_prefix")
               or (hasattr(sys, "base_prefix") and sys.base_prefix != sys.prefix)
               or os.getenv("VIRTUAL_ENV"))
    if not in_venv:
        print("=" * 65)
        print("[!] WARNING: No active virtual environment (venv) detected.")
        print("-" * 65)
        print("    It is strongly recommended to run this tool inside a venv.")
        print("    Quick setup command:")
        print("    > python3 -m venv venv && source venv/bin/activate")
        print("=" * 65 + "\n")

    missing = []
    for module, package in (("requests", "requests"), ("bs4", "beautifulsoup4")):
        try:
            __import__(module)
        except ImportError:
            missing.append(package)
    if missing:
        print("=" * 65)
        print("[X] CRITICAL ERROR: Missing required dependencies.")
        print("-" * 65)
        print("    Please install the missing packages by running:")
        print(f"    > pip install {' '.join(missing)}")
        print("    Optional (much faster HTML parsing): > pip install lxml")
        print("=" * 65 + "\n")
        sys.exit(1)


try:
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
    from bs4 import BeautifulSoup, SoupStrainer
except ImportError:
    pass

try:
    import lxml
    HTML_PARSER = "lxml"
except ImportError:
    HTML_PARSER = "html.parser"

try:
    import pypdf
    logging.getLogger("pypdf").setLevel(logging.CRITICAL)
    PDF_SUPPORT = True
except ImportError:
    PDF_SUPPORT = False


class RangeNotSupported(Exception):
    pass


class RangeFile(io.RawIOBase):
    BLOCK = 64 * 1024

    def __init__(self, getter, url, size, timeout, max_fetch):
        super().__init__()
        self.getter, self.url, self.size = getter, url, size
        self.timeout, self.max_fetch = timeout, max_fetch
        self.pos = 0
        self.blocks = {}
        self.requests = 0
        self.fetched = 0
        self._ensure(max(0, size - 2 * self.BLOCK), size)

    def _ensure(self, start, end):
        first, last = start // self.BLOCK, (end - 1) // self.BLOCK
        missing = [i for i in range(first, last + 1) if i not in self.blocks]
        runs = []
        for i in missing:
            if runs and runs[-1][-1] == i - 1:
                runs[-1].append(i)
            else:
                runs.append([i])
        for run in runs:
            s = run[0] * self.BLOCK
            e = min((run[-1] + 1) * self.BLOCK, self.size) - 1
            if self.fetched + (e - s + 1) > self.max_fetch:
                raise RangeNotSupported("partial read budget exceeded")
            with self.getter(self.url, timeout=self.timeout, stream=True,
                             headers={"Range": f"bytes={s}-{e}", "Accept-Encoding": "identity"}) as r:
                if r.status_code != 206:
                    raise RangeNotSupported(f"server answered {r.status_code} to Range request")
                data = r.raw.read(e - s + 1 + 1, decode_content=False)
            if len(data) != e - s + 1:
                raise RangeNotSupported("unexpected Range response length")
            self.requests += 1
            self.fetched += len(data)
            for n, i in enumerate(run):
                self.blocks[i] = data[n * self.BLOCK:(n + 1) * self.BLOCK]

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.pos

    def seek(self, offset, whence=io.SEEK_SET):
        if whence == io.SEEK_SET:
            self.pos = offset
        elif whence == io.SEEK_CUR:
            self.pos += offset
        else:
            self.pos = self.size + offset
        self.pos = max(0, self.pos)
        return self.pos

    def read(self, n=-1):
        if n is None or n < 0:
            n = self.size - self.pos
        end = min(self.pos + n, self.size)
        if end <= self.pos:
            return b""
        self._ensure(self.pos, end)
        out = bytearray()
        pos = self.pos
        while pos < end:
            i, off = divmod(pos, self.BLOCK)
            chunk = self.blocks[i][off:off + (end - pos)]
            out += chunk
            pos += len(chunk)
        self.pos = end
        return bytes(out)

    def readinto(self, buffer):
        data = self.read(len(buffer))
        buffer[:len(data)] = data
        return len(data)


ZIP_NATIVE_EXTENSIONS = {
    ".docx", ".docm", ".dotx", ".dotm", ".xlsx", ".xlsm", ".xlsb", ".xltx", ".xltm",
    ".pptx", ".pptm", ".ppsx", ".ppsm", ".potx", ".potm", ".vsdx",
    ".odt", ".ott", ".ods", ".ots", ".odp", ".otp", ".odg",
}
ZIP_META_ENTRIES = ("docProps/core.xml", "docProps/app.xml", "docProps/custom.xml", "meta.xml")
XML_TAG_MAP = {
    "creator": "Creator", "lastModifiedBy": "LastModifiedBy", "title": "Title",
    "created": "CreateDate", "modified": "ModifyDate", "creation-date": "CreateDate", "date": "ModifyDate",
    "Application": "Application", "AppVersion": "AppVersion", "Company": "Company", "Manager": "Manager",
    "Template": "Template", "Pages": "Pages", "Slides": "Slides", "initial-creator": "InitialCreator",
    "generator": "Generator", "lastPrinted": "LastPrinted", "HyperlinkBase": "HyperlinkBase",
    "subject": "Subject", "keywords": "Keywords", "description": "Description",
    "printed-by": "LastPrintedBy",
}


def _local(name):
    return name.rsplit("}", 1)[-1]


def xml_props_to_raw(data, raw, group="XML"):
    root = ET.fromstring(data)
    for el in root.iter():
        local = _local(el.tag)
        attrs = {_local(k): v for k, v in el.attrib.items()}
        text = (el.text or "").strip()
        if local in ("property", "user-defined") and ("name" in attrs):
            value = "".join(el.itertext()).strip()
            if value:
                raw[f"{group}:Custom-{attrs['name']}"] = value
        elif local == "document-statistic" and "page-count" in attrs:
            raw.setdefault(f"{group}:PageCount", attrs["page-count"])
        elif local == "template" and "href" in attrs:
            raw.setdefault(f"{group}:Template", attrs["href"])
        elif text and local in XML_TAG_MAP:
            raw.setdefault(f"{group}:{XML_TAG_MAP[local]}", text)


def native_zip_metadata(fileobj):
    raw = {}
    with zipfile.ZipFile(fileobj) as zf:
        names = set(zf.namelist())
        for entry in ZIP_META_ENTRIES:
            if entry in names and zf.getinfo(entry).file_size < 5 * 1024 * 1024:
                try:
                    xml_props_to_raw(zf.read(entry), raw)
                except ET.ParseError:
                    continue
    if not raw:
        raise ValueError("no document properties found in ZIP container")
    return raw


def _pdf_date(value):
    m = re.match(r"D?:?(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?", value)
    if not m:
        return value
    y, mo, d, h, mi, s = (g or "00" for g in m.groups())
    return f"{y}:{mo}:{d} {h}:{mi}:{s}"


def native_pdf_metadata(fileobj):
    if not PDF_SUPPORT:
        raise ValueError("pypdf not installed")
    reader = pypdf.PdfReader(fileobj, strict=False)
    raw = {}
    for key, value in (reader.metadata or {}).items():
        name = key.lstrip("/")
        name = {"CreationDate": "CreateDate", "ModDate": "ModifyDate"}.get(name, name)
        value = str(value)
        raw[f"PDF:{name}"] = _pdf_date(value) if name.endswith("Date") else value
    root = reader.trailer.get("/Root")
    root = root.get_object() if root is not None else {}
    try:
        raw["PDF:PageCount"] = int(root["/Pages"].get_object()["/Count"])
    except Exception:
        pass
    try:
        meta = root.get("/Metadata")
        if meta is not None:
            xmp = ET.fromstring(meta.get_object().get_data())
            creators, agents, texts = [], [], []
            for el in xmp.iter():
                local = _local(el.tag)
                attrs = {_local(k): v for k, v in el.attrib.items()}
                if "softwareAgent" in attrs:
                    agents.append(attrs["softwareAgent"])
                for k, v in attrs.items():
                    texts.append(v)
                    if k in ("CreatorTool", "Producer"):
                        raw.setdefault(f"XMP-xmp:{k}", v)
                if local == "creator":
                    creators += [t.strip() for t in el.itertext() if t.strip()]
                    continue
                text = (el.text or "").strip()
                if not text:
                    continue
                texts.append(text)
                if local == "softwareAgent":
                    agents.append(text)
                elif local in ("CreatorTool", "Producer"):
                    raw.setdefault(f"XMP-xmp:{local}", text)
            if creators:
                raw["XMP-dc:Creator"] = creators
            if agents:
                raw["XMP-xmpMM:HistorySoftwareAgent"] = list(dict.fromkeys(agents))
            if texts:
                raw["XMP-x:AllText"] = texts
    except Exception:
        pass
    if not raw:
        raise ValueError("no PDF metadata found")
    return raw


DOCUMENT_EXTENSIONS = {
    ".pdf": "PDF",
    ".doc": "Word", ".docx": "Word", ".docm": "Word",
    ".dot": "Word", ".dotx": "Word", ".dotm": "Word",
    ".rtf": "RTF",
    ".xls": "Excel", ".xlsx": "Excel", ".xlsm": "Excel", ".xlsb": "Excel",
    ".xlt": "Excel", ".xltx": "Excel", ".xltm": "Excel",
    ".ppt": "PowerPoint", ".pptx": "PowerPoint", ".pptm": "PowerPoint",
    ".pps": "PowerPoint", ".ppsx": "PowerPoint", ".ppsm": "PowerPoint",
    ".pot": "PowerPoint", ".potx": "PowerPoint", ".potm": "PowerPoint",
    ".vsd": "Visio", ".vsdx": "Visio",
    ".pub": "Publisher",
    ".mpp": "MS Project",
    ".odt": "OpenDocument", ".ott": "OpenDocument",
    ".ods": "OpenDocument", ".ots": "OpenDocument",
    ".odp": "OpenDocument", ".otp": "OpenDocument",
    ".odg": "OpenDocument",
    ".indd": "InDesign",
    ".ai": "Illustrator",
    ".eps": "PostScript", ".ps": "PostScript",
    ".psd": "Photoshop",
}
ARCHIVE_EXTENSIONS = {".zip": "Archive", ".rar": "Archive", ".7z": "Archive"}
IMAGE_EXTENSIONS = {
    ".jpg": "Image", ".jpeg": "Image", ".tif": "Image", ".tiff": "Image",
    ".heic": "Image", ".png": "Image", ".svg": "Image",
}

SKIP_CRAWL_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".ico", ".bmp", ".tif", ".tiff", ".heic",
    ".css", ".js", ".mjs", ".map", ".json", ".xml", ".txt", ".csv", ".rss", ".atom",
    ".woff", ".woff2", ".ttf", ".eot", ".otf",
    ".mp3", ".mp4", ".m4a", ".avi", ".mov", ".webm", ".wmv", ".flv", ".mkv", ".wav", ".ogg",
    ".exe", ".msi", ".dmg", ".iso", ".apk", ".gz", ".tgz", ".bz2", ".tar", ".bin",
    ".zip", ".rar", ".7z",
}

CONTENT_TYPE_EXT = {
    "application/pdf": ".pdf",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.ms-powerpoint": ".ppt",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/vnd.oasis.opendocument.text": ".odt",
    "application/vnd.oasis.opendocument.spreadsheet": ".ods",
    "application/vnd.oasis.opendocument.presentation": ".odp",
    "application/rtf": ".rtf",
    "text/rtf": ".rtf",
}

CC_SECOND_LEVEL = {"com", "net", "org", "gob", "gov", "edu", "co", "ac", "mil", "nom", "info", "biz", "or", "ne", "go"}


USER_TAGS = {
    "Author", "Creator", "LastModifiedBy", "InitialCreator", "Initial-creator", "Manager",
    "Artist", "XPAuthor", "OwnerName", "CameraOwnerName", "For", "By-line", "Writer-Editor",
    "Contributor", "LastSavedBy",
}
SOFTWARE_TAGS = {
    "Producer", "Software", "Application", "CreatorTool", "Generator",
    "HistorySoftwareAgent", "CreatorApplication", "ProcessingSoftware",
}
SOFTWARE_CREATOR_GROUPS = {"PDF", "PostScript"}
COMPANY_TAGS = {"Company"}
TEMPLATE_TAGS = {"Template", "AttachedTemplate"}
HOST_TAGS = {"HostComputer"}
PAGE_TAGS = ("PageCount", "Pages", "Slides")
CREATED_TAGS = ("CreateDate", "DateTimeOriginal", "CreationDate")
MODIFIED_TAGS = ("ModifyDate", "MetadataDate")
SKIP_GROUPS = {"System", "File", "ExifTool"}
JUNK_VALUES = {"", "unknown", "user", "usuario", "-", "n/a", "none", "null", "(none)"}

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PATH_RE = re.compile(
    r"(?:[A-Za-z]:\\[^\"<>|\r\n\t]+"
    r"|\\\\[\w.$-]+\\[^\"<>|\r\n\t]+"
    r"|/(?:Users|home|Volumes|mnt)/[^\"<>|\r\n\t]+)"
)
PATH_USER_RE = re.compile(
    r"(?:[A-Za-z]:\\(?:Users|Documents and Settings)\\([^\\]+)"
    r"|/(?:Users|home)/([^/]+))", re.I)
PATH_USER_IGNORE = {"public", "default", "all users", "shared", "administrator.default"}

REPORT_FIELDS = [
    "Title", "Users", "Software", "Company", "Template", "Emails", "Paths",
    "Hosts", "Device", "GPS", "Created", "Modified", "Pages", "FileSize",
]
INTEL_FIELDS = ["Users", "Software", "Company", "Emails", "Paths", "Hosts", "Device", "GPS", "Template"]


def clean_value(value):
    value = str(value)
    value = re.sub(r"^[\xfe\xff\ufffe\ufeff]+", "", value)
    value = value.replace("\u00fe\u00ff", "").replace("\xff\xfe", "")
    value = re.sub(r"[\x00-\x1f\x7f-\x9f]", "", value)
    return value.strip()


def is_junk(value):
    return value.lower() in JUNK_VALUES or value.startswith("(Binary data")


def as_list(value):
    if isinstance(value, list):
        return value
    return [value]


def parse_exiftool_record(raw, ext):
    rec = defaultdict(list)
    for key, value in raw.items():
        if key == "SourceFile":
            continue
        group, _, tag = key.rpartition(":")
        tag = re.sub(r" \(\d+\)$", "", tag)
        if group in SKIP_GROUPS:
            if group == "ExifTool" and tag in ("Error", "Warning"):
                rec["_warnings"].append(clean_value(value))
            continue

        for item in as_list(value):
            text = clean_value(item)
            if not text or is_junk(text):
                continue

            rec["Emails"].extend(EMAIL_RE.findall(text))
            for path in PATH_RE.findall(text):
                rec["Paths"].append(path.strip())

            if tag == "Creator" and (group in SOFTWARE_CREATOR_GROUPS
                                     or (ext in (".pdf", ".ai", ".eps", ".ps") and not group.startswith("XMP"))):
                rec["Software"].append(text)
            elif tag in USER_TAGS:
                if not PATH_RE.fullmatch(text):
                    rec["Users"].append(text)
            elif tag in SOFTWARE_TAGS:
                rec["Software"].append(text)
            elif tag in COMPANY_TAGS:
                rec["Company"].append(text)
            elif tag in TEMPLATE_TAGS:
                rec["Template"].append(text)
            elif tag in HOST_TAGS:
                rec["Hosts"].append(text)
            elif tag == "Title":
                rec["Title"].append(text)
            elif tag in PAGE_TAGS:
                rec["_pages"].append(text)
            elif tag in CREATED_TAGS:
                rec["_created"].append(text)
            elif tag in MODIFIED_TAGS:
                rec["_modified"].append(text)
            elif tag in ("Make", "Model"):
                rec["_device_" + tag].append(text)
            elif tag == "GPSPosition":
                rec["GPS"].append(text)
            elif tag == "AppVersion":
                rec["_appversion"].append(text)

    for path in rec["Paths"]:
        for m in PATH_USER_RE.finditer(path):
            user = (m.group(1) or m.group(2) or "").strip()
            if user and user.lower() not in PATH_USER_IGNORE:
                rec["Users"].append(user)

    if rec["_appversion"]:
        version = rec["_appversion"][0]
        rec["Software"] = [f"{s} ({version})" if s.lower().startswith("microsoft") and version not in s else s
                           for s in rec["Software"]]

    make = rec["_device_Make"][0] if rec["_device_Make"] else ""
    model = rec["_device_Model"][0] if rec["_device_Model"] else ""
    device = model if make and model.lower().startswith(make.lower()) else f"{make} {model}".strip()
    out = {}
    for field in ("Users", "Software", "Company", "Template", "Emails", "Paths", "Hosts", "GPS"):
        values = list(dict.fromkeys(v for v in rec[field] if v))
        if values:
            out[field] = values
    if device:
        out["Device"] = [device]
    if rec["Title"]:
        out["Title"] = rec["Title"][0]
    if rec["_pages"]:
        out["Pages"] = rec["_pages"][0]
    if rec["_created"]:
        out["Created"] = rec["_created"][0]
    if rec["_modified"]:
        out["Modified"] = rec["_modified"][0]
    if rec["_warnings"] and len(out) == 0:
        out["Error"] = "; ".join(dict.fromkeys(rec["_warnings"]))
    return out


def normalize_url(url):
    url, _ = urldefrag(url.strip())
    p = urlparse(url)
    scheme = p.scheme.lower()
    netloc = p.netloc.lower()
    if (scheme == "http" and netloc.endswith(":80")) or (scheme == "https" and netloc.endswith(":443")):
        netloc = netloc.rsplit(":", 1)[0]
    path = p.path or "/"
    return urlunparse((scheme, netloc, path, p.params, p.query, ""))


def path_extension(url):
    return os.path.splitext(unquote(urlparse(url).path))[1].lower()


def registrable_domain(host):
    host = (host or "").lower().split(":")[0]
    if re.fullmatch(r"[\d.]+", host) or ":" in host:
        return host
    if host.startswith("www."):
        host = host[4:]
    labels = host.split(".")
    if len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in CC_SECOND_LEVEL:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def display_name(url):
    parsed = urlparse(url)
    name = os.path.basename(unquote(parsed.path))
    if not os.path.splitext(name)[1] or name.lower().endswith((".php", ".asp", ".aspx", ".jsp", ".cgi")):
        for _, value in parse_qsl(parsed.query):
            if re.search(r"\.[A-Za-z0-9]{2,5}$", value):
                return os.path.basename(value.replace("\\", "/"))
    return name or url


def url_sort_key(item):
    url, ext = item
    return (ext, url.lower())


class FileFinder:
    def __init__(self, threads=10, timeout=15, dl_timeout=45, extract_metadata=True,
                 max_file_size_mb=50, max_pages=0, use_seeds=True, include_images=False,
                 include_archives=True, delay=0.0, verbose=False, use_range=True,
                 range_threshold_mb=1.0, has_exiftool=True):
        self.threads = max(1, threads)
        self.timeout = timeout
        self.dl_timeout = dl_timeout
        self.extract_metadata = extract_metadata
        self.max_file_size = max_file_size_mb * 1024 * 1024
        self.max_pages = max_pages
        self.use_seeds = use_seeds
        self.delay = delay
        self.verbose = verbose
        self.use_range = use_range
        self.range_threshold = int(range_threshold_mb * 1024 * 1024)
        self.has_exiftool = has_exiftool

        self.target_extensions = dict(DOCUMENT_EXTENSIONS)
        if include_archives:
            self.target_extensions.update(ARCHIVE_EXTENSIONS)
        if include_images:
            self.target_extensions.update(IMAGE_EXTENSIONS)
        self.skip_extensions = SKIP_CRAWL_EXTENSIONS - set(self.target_extensions)

        exts = sorted((e[1:] for e in self.target_extensions), key=len, reverse=True)
        self.file_url_re = re.compile(
            rb"(?<=[\s\"'(=,\[])((?:https?:)?/[^\s\"'<>()\[\]{}|^`]*?\.(?:"
            + "|".join(exts).encode() + rb")(?:\?[^\s\"'<>()]*)?)(?=[\s\"'<>()#\],]|$)",
            re.I,
        )
        self.query_ext_re = re.compile(r"\.(?:" + "|".join(exts) + r")$", re.I)
        self.strainer = SoupStrainer(["a", "area", "iframe", "frame", "embed", "object",
                                      "link", "img", "source", "base"])

        self.session = requests.Session()
        retry = Retry(total=2, backoff_factor=0.3, status_forcelist=(429, 500, 502, 503, 504),
                      allowed_methods=("GET", "HEAD"), raise_on_status=False)
        adapter = HTTPAdapter(pool_connections=20, pool_maxsize=self.threads * 2 + 4, max_retries=retry)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
        self.session.headers.update({
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        })

        self.print_lock = threading.Lock()
        self.meta_lock = threading.Lock()
        self.reset()

    def reset(self):
        self.visited = set()
        self.found_files = {}
        self.files_with_metadata = {}
        self.downloaded = {}
        self.pages_crawled = 0
        self.range_stats = {"files": 0, "size": 0, "fetched": 0}
        self._counter = itertools.count()
        self.scope = None

    def log(self, msg, verbose_only=False):
        if verbose_only and not self.verbose:
            return
        with self.print_lock:
            print(msg, flush=True)

    def in_scope(self, url):
        host = urlparse(url).hostname or ""
        return host == self.scope or host.endswith("." + self.scope)

    def file_extension(self, url):
        ext = path_extension(url)
        if ext in self.target_extensions:
            return ext
        for _, value in parse_qsl(urlparse(url).query):
            if self.query_ext_re.search(value):
                ext = os.path.splitext(value)[1].lower()
                if ext in self.target_extensions:
                    return ext
        return None

    def _get(self, url, **kwargs):
        if self.delay:
            time.sleep(self.delay)
        return self.session.get(url, verify=False, allow_redirects=True, **kwargs)

    def fetch_page(self, url):
        files, links = set(), set()
        try:
            with self._get(url, timeout=(10, self.timeout), stream=True) as r:
                if r.status_code >= 400:
                    self.log(f"[-] {r.status_code} {url}", verbose_only=True)
                    return files, links
                ctype = r.headers.get("Content-Type", "").split(";")[0].strip().lower()

                if ctype in CONTENT_TYPE_EXT and CONTENT_TYPE_EXT[ctype] in self.target_extensions:
                    files.add((normalize_url(r.url), CONTENT_TYPE_EXT[ctype]))
                    return files, links
                if ctype and "html" not in ctype:
                    return files, links

                body = bytearray()
                for chunk in r.iter_content(65536):
                    body.extend(chunk)
                    if len(body) > 10 * 1024 * 1024:
                        break
                final_url = r.url
        except Exception as e:
            self.log(f"[-] Error analyzing {url}: {e}", verbose_only=True)
            return files, links

        if final_url != url and not self.in_scope(final_url):
            return files, links
        self.parse_html(bytes(body), final_url, files, links)
        return files, links

    def _classify(self, value, base_url, files, links, follow):
        value = value.strip()
        if not value or value.startswith(("javascript:", "mailto:", "tel:", "data:", "#")):
            return
        url = normalize_url(urljoin(base_url, value))
        if not url.startswith(("http://", "https://")):
            return
        ext = self.file_extension(url)
        if ext:
            files.add((url, ext))
        elif follow and path_extension(url) not in self.skip_extensions and self.in_scope(url):
            links.add(url)

    def parse_html(self, content, base_url, files, links):
        try:
            soup = BeautifulSoup(content, HTML_PARSER, parse_only=self.strainer)
        except Exception:
            soup = None
        if soup is not None:
            base_tag = soup.find("base", href=True)
            if base_tag:
                base_url = urljoin(base_url, base_tag["href"])
            for tag in soup.find_all(True):
                follow = tag.name in ("a", "area", "iframe", "frame")
                for attr in ("href", "src", "data"):
                    value = tag.get(attr)
                    if isinstance(value, str):
                        self._classify(value, base_url, files, links, follow)

        text = content.replace(b"\\/", b"/").replace(b"&amp;", b"&")
        for m in self.file_url_re.finditer(text):
            self._classify(m.group(1).decode("utf-8", "ignore"), base_url, files, set(), False)

    def add_file(self, url, ext, dl_pool):
        if url in self.found_files:
            return
        self.found_files[url] = ext
        self.log(f"    [+] File found ({ext}): {url}", verbose_only=True)
        if dl_pool is not None:
            dl_pool.submit(self.download, url, ext)

    def discover_seeds(self, start_url):
        p = urlparse(start_url)
        root = f"{p.scheme}://{p.netloc}"
        pages, files = set(), set()

        sitemaps = {root + "/sitemap.xml", root + "/sitemap_index.xml", root + "/wp-sitemap.xml"}
        try:
            r = self._get(root + "/robots.txt", timeout=(10, self.timeout))
            if r.ok and "html" not in r.headers.get("Content-Type", ""):
                for line in r.text.splitlines():
                    if line.lower().startswith("sitemap:"):
                        sitemaps.add(line.split(":", 1)[1].strip())
        except Exception:
            pass

        seen, pending, fetched = set(), set(sitemaps), 0
        with ThreadPoolExecutor(self.threads) as pool:
            while pending and fetched < 200:
                batch = list(pending)[: 200 - fetched]
                pending.clear()
                seen.update(batch)
                fetched += len(batch)
                for sm_url, content in zip(batch, pool.map(self._fetch_sitemap, batch)):
                    if not content:
                        continue
                    locs = [unquote_xml(m) for m in re.findall(rb"<loc>\s*(.*?)\s*</loc>", content, re.S)]
                    is_index = b"<sitemapindex" in content[:2000]
                    for loc in locs:
                        loc = normalize_url(loc)
                        if is_index or loc.endswith((".xml", ".xml.gz")):
                            if loc not in seen:
                                pending.add(loc)
                            continue
                        ext = self.file_extension(loc)
                        if ext:
                            files.add((loc, ext))
                        elif self.in_scope(loc) and path_extension(loc) not in self.skip_extensions:
                            pages.add(loc)

        files.update(self._wp_media(root))
        return pages, files

    def _fetch_sitemap(self, url):
        try:
            r = self._get(url, timeout=(10, self.timeout))
            if not r.ok:
                return None
            content = r.content
            if url.endswith(".gz") or content[:2] == b"\x1f\x8b":
                content = gzip.decompress(content)
            if b"<urlset" not in content[:5000] and b"<sitemapindex" not in content[:5000]:
                return None
            return content
        except Exception:
            return None

    def _wp_media(self, root):
        found = set()
        api = root + "/wp-json/wp/v2/media?per_page=100&_fields=source_url&page={}"

        def fetch(page):
            try:
                r = self._get(api.format(page), timeout=(10, self.timeout))
                if r.ok and "json" in r.headers.get("Content-Type", ""):
                    return r, r.json()
            except Exception:
                pass
            return None, None

        first, data = fetch(1)
        if not first or not isinstance(data, list):
            return found
        try:
            total_pages = min(int(first.headers.get("X-WP-TotalPages", "1")), 200)
        except ValueError:
            total_pages = 1
        results = [data]
        if total_pages > 1:
            with ThreadPoolExecutor(self.threads) as pool:
                results += [d for _, d in pool.map(fetch, range(2, total_pages + 1))]
        for items in results:
            for item in items or []:
                src = item.get("source_url") if isinstance(item, dict) else None
                if src:
                    url = normalize_url(src)
                    ext = self.file_extension(url)
                    if ext:
                        found.add((url, ext))
        if found:
            self.log(f"[+] WordPress media API: {len(found)} target files listed")
        return found

    def native_capable(self, ext):
        return ext in ZIP_NATIVE_EXTENSIONS or (ext == ".pdf" and PDF_SUPPORT)

    def native_extract(self, fileobj, ext):
        raw = native_pdf_metadata(fileobj) if ext == ".pdf" else native_zip_metadata(fileobj)
        return parse_exiftool_record(raw, ext)

    def _store(self, url, record, local=None):
        with self.meta_lock:
            self.files_with_metadata[url] = record
            if local:
                self.downloaded[url] = local

    def download(self, url, ext):
        filename = display_name(url)
        base = {"URL": url, "Filename": filename, "Ext": ext}
        try:
            with self._get(url, timeout=(10, self.dl_timeout), stream=True) as r:
                r.raise_for_status()
                length = r.headers.get("Content-Length", "")
                length = int(length) if length.isdigit() else None
                ranges_ok = (self.use_range and length and self.native_capable(ext)
                             and r.headers.get("Content-Encoding", "identity").lower() in ("", "identity")
                             and (length > self.range_threshold or length > self.max_file_size)
                             and (r.headers.get("Accept-Ranges", "").lower() == "bytes"
                                  or length > self.max_file_size))
                if not ranges_ok:
                    self._full_download(url, ext, r, length, base)
                    return
            if self._range_download(url, ext, length, base):
                return
            with self._get(url, timeout=(10, self.dl_timeout), stream=True) as r:
                r.raise_for_status()
                length = r.headers.get("Content-Length", "")
                self._full_download(url, ext, r, int(length) if length.isdigit() else None, base)
        except Exception as e:
            self._store(url, {**base, "Error": f"Download failed: {e}"})
            self.log(f"    [-] {filename}: {e}")

    def _range_download(self, url, ext, length, base):
        try:
            rf = RangeFile(self._get, url, length, timeout=(10, self.dl_timeout),
                           max_fetch=max(2 * 1024 * 1024, length // 10))
            record = self.native_extract(rf, ext)
        except Exception as e:
            self.log(f"    [~] {base['Filename']}: partial read failed ({e}), full download", verbose_only=True)
            return False
        record.update(base)
        record["FileSize"] = f"{length:,} bytes (read {rf.fetched:,} via {rf.requests} Range requests)"
        record["Mode"] = "range"
        self._store(url, record)
        with self.meta_lock:
            self.range_stats["files"] += 1
            self.range_stats["size"] += length
            self.range_stats["fetched"] += rf.fetched
        self.log(f"    [✓] Range read: {base['Filename']} ({rf.fetched:,} of {length:,} bytes)")
        return True

    def _full_download(self, url, ext, r, length, base):
        filename = base["Filename"]
        if length and length > self.max_file_size:
            raise ValueError(f"File too large ({length:,} bytes > {self.max_file_size // (1024 * 1024)}MB)")
        local = os.path.join(self.tmpdir, f"{next(self._counter):06d}{ext}")
        size = 0
        try:
            with open(local, "wb") as fh:
                for chunk in r.iter_content(65536):
                    size += len(chunk)
                    if size > self.max_file_size:
                        raise ValueError(f"File too large (>{self.max_file_size // (1024 * 1024)}MB)")
                    fh.write(chunk)
            if size == 0:
                raise ValueError("Empty file")
            with open(local, "rb") as fh:
                head = fh.read(512).lstrip().lower()
            if ext != ".svg" and (head.startswith(b"<!doctype html") or head.startswith(b"<html")):
                raise ValueError("Server returned an HTML page instead of the file (soft 404 / login)")
        except Exception:
            if os.path.exists(local):
                os.remove(local)
            raise

        record = {**base, "FileSize": f"{size:,} bytes", "Mode": "full"}
        if self.has_exiftool:
            self._store(url, record, local)
        else:
            try:
                if not self.native_capable(ext):
                    raise ValueError("format needs exiftool")
                with open(local, "rb") as fh:
                    record.update(self.native_extract(fh, ext))
            except Exception as e:
                record["Error"] = f"Metadata not extracted: {e}"
            finally:
                os.remove(local)
            self._store(url, record)
        self.log(f"    [✓] Downloaded: {filename} ({size:,} bytes)")

    def run_exiftool(self):
        items = list(self.downloaded.items())
        if not items:
            return
        self.log(f"\n[+] Extracting metadata from {len(items)} files with exiftool (batch mode)...")
        by_name = {os.path.basename(path): url for url, path in items}
        chunks = [items[i:i + 200] for i in range(0, len(items), 200)]

        def run_chunk(index_chunk):
            index, chunk = index_chunk
            argfile = os.path.join(self.tmpdir, f"_args_{index}.txt")
            with open(argfile, "w", encoding="utf-8") as fh:
                fh.write("\n".join(path for _, path in chunk) + "\n")
            try:
                proc = subprocess.run(
                    ["exiftool", "-json", "-a", "-G1", "-q", "-q", "-charset", "filename=utf8",
                     "-api", "LargeFileSupport=1", "-@", argfile],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=600,
                )
                out = proc.stdout.decode("utf-8", "replace").strip()
                return json.loads(out) if out else []
            except Exception as e:
                self.log(f"    [-] exiftool error: {e}")
                return []

        workers = min(4, os.cpu_count() or 2, len(chunks))
        with ThreadPoolExecutor(workers) as pool:
            for raws in pool.map(run_chunk, enumerate(chunks)):
                for raw in raws:
                    url = by_name.get(os.path.basename(str(raw.get("SourceFile", ""))))
                    if not url:
                        continue
                    record = self.files_with_metadata[url]
                    record.update(parse_exiftool_record(raw, record["Ext"]))

    def find_files(self, start_url, max_depth=2):
        self.reset()
        start_url = normalize_url(start_url)
        self.scope = registrable_domain(urlparse(start_url).hostname)
        self.tmpdir = tempfile.mkdtemp(prefix="metadata_")
        started = time.time()

        dl_pool = ThreadPoolExecutor(self.threads) if self.extract_metadata else None
        queue = deque([(start_url, 0)])
        self.visited.add(start_url)

        try:
            if self.use_seeds:
                seed_pages, seed_files = self.discover_seeds(start_url)
                for url, ext in sorted(seed_files):
                    self.add_file(url, ext, dl_pool)
                new_pages = [u for u in sorted(seed_pages) if u not in self.visited]
                for url in new_pages:
                    self.visited.add(url)
                    queue.append((url, max_depth))
                self.log(f"[+] Seeds: {len(new_pages)} pages and {len(seed_files)} files from sitemaps/APIs")

            with ThreadPoolExecutor(self.threads) as crawl_pool:
                pending = {}
                last_report = 0
                while queue or pending:
                    while queue and len(pending) < self.threads * 2:
                        if self.max_pages and self.pages_crawled >= self.max_pages:
                            self.log(f"[!] --max-pages limit ({self.max_pages}) reached, "
                                     f"{len(queue)} pages left in queue")
                            queue.clear()
                            break
                        url, depth = queue.popleft()
                        self.pages_crawled += 1
                        self.log(f"[+] Analyzing ({self.pages_crawled}): {url}", verbose_only=True)
                        pending[crawl_pool.submit(self.fetch_page, url)] = depth
                    if not pending:
                        break
                    done, _ = wait(pending, return_when=FIRST_COMPLETED)
                    for future in done:
                        depth = pending.pop(future)
                        files, links = future.result()
                        for url, ext in files:
                            self.add_file(url, ext, dl_pool)
                        if depth < max_depth:
                            for link in links:
                                if link not in self.visited:
                                    self.visited.add(link)
                                    queue.append((link, depth + 1))
                    if not self.verbose and self.pages_crawled - last_report >= 25:
                        last_report = self.pages_crawled
                        self.log(f"[*] Pages: {self.pages_crawled} | queue: {len(queue)} | "
                                 f"files: {len(self.found_files)}")

            self.log(f"[+] Crawl finished: {self.pages_crawled} pages, {len(self.found_files)} files "
                     f"({time.time() - started:.1f}s)")
            if dl_pool is not None:
                self.log("[+] Waiting for pending downloads...")
                dl_pool.shutdown(wait=True)
                dl_pool = None
                self.run_exiftool()
        finally:
            if dl_pool is not None:
                dl_pool.shutdown(wait=False, cancel_futures=True)
            shutil.rmtree(self.tmpdir, ignore_errors=True)

        rs = self.range_stats
        if rs["files"]:
            self.log(f"[+] Range mode: {rs['files']} files read partially -> "
                     f"{rs['fetched'] / 1048576:.1f}MB fetched instead of {rs['size'] / 1048576:.1f}MB")
        self.log(f"[+] Total time: {time.time() - started:.1f}s")
        return set(self.found_files.items())

    def _get_output_dir(self, domain):
        if not domain.startswith(("http://", "https://")):
            domain = f"http://{domain}"
        netloc = urlparse(domain).netloc
        if netloc.startswith("www."):
            netloc = netloc[4:]
        folder_name = f"{netloc.replace(':', '_')}_results"
        os.makedirs(folder_name, exist_ok=True)
        return folder_name

    @staticmethod
    def _domain_name(domain):
        return urlparse(domain if "://" in domain else f"http://{domain}").netloc.replace(":", "_")

    def _filtered(self, url):
        record = self.files_with_metadata.get(url, {})
        out = {}
        for field in REPORT_FIELDS:
            value = record.get(field)
            if not value:
                continue
            out[field] = ", ".join(value) if isinstance(value, list) else str(value)
        return out

    @staticmethod
    def _md(value):
        return str(value).replace("|", "&#124;").replace("\n", " ")

    def generate_urls_txt_file(self, files, domain):
        output_dir = self._get_output_dir(domain)
        filename = os.path.join(output_dir, f"{self._domain_name(domain)}_url-metadata.txt")
        try:
            with open(filename, "w", encoding="utf-8") as f:
                for url, _ in sorted(files, key=url_sort_key):
                    f.write(f"{url}\n")
            self.log(f"[+] URLs text file generated: {filename}")
            return filename
        except Exception as e:
            self.log(f"[-] Error generating URLs text file: {e}")
            return None

    def generate_summary_file(self, files, domain):
        output_dir = self._get_output_dir(domain)
        filename = os.path.join(output_dir, f"{self._domain_name(domain)}_idem-meta-summary.md")
        by_ext = defaultdict(list)
        for url, ext in files:
            by_ext[ext].append(url)
        try:
            with open(filename, "w", encoding="utf-8") as f:
                f.write(f"# File Metadata Summary - {domain}\n\n")
                f.write(f"**Analyzed Domain:** {domain}\n")
                f.write(f"**Analysis Date:** {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")
                for ext in sorted(by_ext):
                    urls = sorted(by_ext[ext], key=str.lower)
                    f.write(f"## {ext} - {self.target_extensions.get(ext, 'Unknown')} ({len(urls)} files)\n\n")
                    for url in urls:
                        name = display_name(url)
                        f.write(f"### File: {self._md(name)}\n\n**Link:** [{self._md(name)}]({url})\n\n")
                        record = self.files_with_metadata.get(url)
                        filtered = self._filtered(url)
                        if record is None:
                            f.write("*Metadata extraction disabled*\n")
                        elif any(k not in ("FileSize",) for k in filtered):
                            f.write("| Field | Value |\n|-------|-------|\n")
                            for key, value in filtered.items():
                                f.write(f"| {key} | {self._md(value)} |\n")
                        else:
                            f.write("*No interesting metadata found*\n")
                            if "Error" in record:
                                f.write(f"*Error: {self._md(record['Error'])}*\n")
                        f.write("\n---\n\n")
            self.log(f"[+] Summary file generated: {filename}")
            return filename
        except Exception as e:
            self.log(f"[-] Error generating summary file: {e}")
            return None

    def intel(self, files):
        agg = {field: defaultdict(list) for field in INTEL_FIELDS}
        for url, _ in sorted(files, key=url_sort_key):
            record = self.files_with_metadata.get(url, {})
            for field in INTEL_FIELDS:
                for value in record.get(field, []):
                    agg[field][value].append(display_name(url))
        return agg

    def generate_executive_summary(self, files, domain):
        output_dir = self._get_output_dir(domain)
        filename = os.path.join(output_dir, f"{self._domain_name(domain)}_meta_executive-summary.md")
        ext_count = Counter(ext for _, ext in files)
        records = [self.files_with_metadata[u] for u, _ in files if u in self.files_with_metadata]
        with_meta = sum(1 for r in records if any(f in r for f in INTEL_FIELDS + ["Title"]))
        errors = sum(1 for r in records if "Error" in r)
        downloaded = sum(1 for r in records if "FileSize" in r)
        agg = self.intel(files)
        titles = {
            "Users": "Users / Authors", "Software": "Software & Versions", "Company": "Companies",
            "Emails": "E-mail Addresses", "Paths": "Internal Paths", "Hosts": "Host Computers",
            "Device": "Devices (camera / scanner)", "GPS": "GPS Positions", "Template": "Templates",
        }
        try:
            with open(filename, "w", encoding="utf-8") as f:
                f.write(f"# Metadata Executive Summary - {domain}\n\n")
                f.write(f"**Analyzed Domain:** {domain}\n")
                f.write(f"**Analysis Date:** {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")
                f.write("## General Statistics\n\n| Metric | Value |\n|--------|-------|\n")
                f.write(f"| Pages crawled | {self.pages_crawled} |\n")
                f.write(f"| Total files found | {len(files)} |\n")
                f.write(f"| Files downloaded | {downloaded} |\n")
                f.write(f"| Files with useful metadata | {with_meta} |\n")
                f.write(f"| Download / parse errors | {errors} |\n")
                for field in ("Users", "Software", "Emails", "Paths"):
                    f.write(f"| Unique {titles[field].lower()} | {len(agg[field])} |\n")
                f.write("\n## Distribution by Extension\n\n| Extension | Type | Count |\n|-----------|------|-------|\n")
                for ext in sorted(ext_count):
                    f.write(f"| {ext} | {self.target_extensions.get(ext, 'Unknown')} | {ext_count[ext]} |\n")
                f.write("\n")
                for field in INTEL_FIELDS:
                    if not agg[field]:
                        continue
                    f.write(f"## {titles[field]}\n\n| Value | Files | Examples |\n|-------|-------|----------|\n")
                    for value, names in sorted(agg[field].items(), key=lambda x: (-len(x[1]), x[0].lower())):
                        sample = ", ".join(names[:3]) + ("..." if len(names) > 3 else "")
                        f.write(f"| {self._md(value)} | {len(names)} | {self._md(sample)} |\n")
                    f.write("\n")
            self.log(f"[+] Executive summary generated: {filename}")
            return filename
        except Exception as e:
            self.log(f"[-] Error generating executive summary: {e}")
            return None


def unquote_xml(raw):
    text = raw.decode("utf-8", "ignore").strip()
    if text.startswith("<![CDATA[") and text.endswith("]]>"):
        text = text[9:-3]
    return (text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
            .replace("&quot;", '"').replace("&apos;", "'"))


def main():
    banner = rf"""
                _          _       _
               | |        | |     | |
 _ __ ___   ___| |_ __ _  __| | __ _| |_ __ _
| '_ ` _ \ / _ \ __/ _` |/ _` |/ _` | __/ _` |
| | | | | |  __/ || (_| | (_| | (_| | || (_| |
|_| |_| |_|\___|\__\__,_|\__,_|\__,_|\__\__,_| v{VERSION}
         Web File Recon & Metadata Extractor
                     by @far00t01
    """
    print(banner)

    parser = argparse.ArgumentParser(usage="python3 metadata.py [domain].com",
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("domain", nargs="?", help="Domain to analyze (e.g., domain.com)")
    parser.add_argument("-f", "--file", help="File with a list of domains (one per line)")
    parser.add_argument("-t", "--threads", type=int, default=10, help="Crawl/download threads (default: 10)")
    parser.add_argument("-d", "--depth", type=int, default=2, help="Maximum crawling depth (default: 2)")
    parser.add_argument("-o", "--output", help="Save all metadata as JSON to this file")
    parser.add_argument("--max-pages", type=int, default=0, help="Stop crawling after N pages (default: 0 = no limit)")
    parser.add_argument("--timeout", type=int, default=15, help="Timeout for HTML pages in seconds (default: 15)")
    parser.add_argument("--metadata-timeout", type=int, default=45, help="Timeout for file downloads (default: 45)")
    parser.add_argument("--max-file-size", type=int, default=50, help="Maximum file size in MB (default: 50)")
    parser.add_argument("--delay", type=float, default=0.0, help="Delay in seconds before each request, per thread")
    parser.add_argument("--images", action="store_true", help="Also collect images (jpg, tiff, heic, png, svg) for EXIF")
    parser.add_argument("--no-archives", action="store_true", help="Skip .zip/.rar/.7z files")
    parser.add_argument("--no-range", action="store_true", help="Always download full files (disable HTTP Range reads)")
    parser.add_argument("--range-threshold", type=float, default=1.0,
                        help="Use partial Range reads for PDF/OOXML/ODF files above this size in MB (default: 1)")
    parser.add_argument("--no-seeds", action="store_true", help="Do not use robots.txt, sitemaps or WordPress media API")
    parser.add_argument("--no-summary", action="store_true", help="Do not generate the detailed per-file summary")
    parser.add_argument("--no-metadata", action="store_true", help="Do not extract metadata (find files only)")
    parser.add_argument("--executive-only", action="store_true", help="Generate executive summary only")
    parser.add_argument("-v", "--verbose", action="store_true", help="Print every analyzed page and found file")
    args = parser.parse_args()

    if not args.domain and not args.file:
        parser.print_help()
        sys.exit(1)

    has_exiftool = bool(shutil.which("exiftool"))
    if not args.no_metadata and not has_exiftool:
        print("[!] exiftool is not installed or not in PATH -> only PDF (pypdf) and OOXML/ODF")
        print("    metadata will be extracted natively. Install exiftool for every format:")
        print("    apt install libimage-exiftool-perl | brew install exiftool\n")

    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    domains = []
    if args.domain:
        domains.append(args.domain)
    if args.file:
        try:
            with open(args.file, "r", encoding="utf-8") as fh:
                domains.extend(line.strip() for line in fh if line.strip() and not line.startswith("#"))
        except FileNotFoundError:
            print(f"[-] Error: File {args.file} not found")
            sys.exit(1)
    domains = [d if d.startswith(("http://", "https://")) else f"https://{d}" for d in domains]
    domains = list(dict.fromkeys(domains))

    finder = FileFinder(
        threads=args.threads, timeout=args.timeout, dl_timeout=args.metadata_timeout,
        extract_metadata=not args.no_metadata, max_file_size_mb=args.max_file_size,
        max_pages=args.max_pages, use_seeds=not args.no_seeds, include_images=args.images,
        include_archives=not args.no_archives, delay=args.delay, verbose=args.verbose,
        use_range=not args.no_range, range_threshold_mb=args.range_threshold, has_exiftool=has_exiftool,
    )

    print(f"[+] Target extensions ({len(finder.target_extensions)}): "
          f"{' '.join(sorted(finder.target_extensions))}")
    print(f"[+] Metadata extraction: {'ACTIVATED' if not args.no_metadata else 'DEACTIVATED'}"
          f" | exiftool: {'yes' if has_exiftool else 'NO'} | pypdf: {'yes' if PDF_SUPPORT else 'no'}"
          f" | Range reads: {'off' if args.no_range else f'> {args.range_threshold}MB'}")
    print(f"[+] HTML parser: {HTML_PARSER}{'' if HTML_PARSER == 'lxml' else ' (pip install lxml for faster parsing)'}")
    print(f"[+] Threads: {args.threads} | depth: {args.depth} | max file size: {args.max_file_size}MB\n")

    generated = defaultdict(list)
    json_dump = {}

    for domain in domains:
        print(f"\n[+] Starting analysis of: {domain}")
        files = finder.find_files(domain, max_depth=args.depth)

        print(f"\n[+] Results for {domain}:")
        print("=" * 70)
        if files:
            by_ext = defaultdict(list)
            for url, ext in files:
                by_ext[ext].append(url)
            for ext in sorted(by_ext):
                urls = sorted(by_ext[ext], key=str.lower)
                print(f"\n{ext} ({len(urls)}):")
                for url in urls[:3]:
                    print(f"  {url}")
                if len(urls) > 3:
                    print(f"  ... and {len(urls) - 3} more")
            if not args.no_metadata:
                agg = finder.intel(files)
                for field in ("Users", "Software", "Emails", "Paths"):
                    if agg[field]:
                        top = sorted(agg[field].items(), key=lambda x: -len(x[1]))[:10]
                        print(f"\n{field} ({len(agg[field])}): " + ", ".join(v for v, _ in top)
                              + (" ..." if len(agg[field]) > 10 else ""))
        else:
            print("No files found")
        print("=" * 70)

        if not args.no_summary and not args.executive_only:
            generated["Summary files"].append(finder.generate_summary_file(files, domain))
        generated["URL list files"].append(finder.generate_urls_txt_file(files, domain))
        generated["Executive summaries"].append(finder.generate_executive_summary(files, domain))

        if args.output:
            json_dump[domain] = [
                finder.files_with_metadata.get(url, {"URL": url, "Ext": ext})
                for url, ext in sorted(files, key=url_sort_key)
            ]

    for label, paths in generated.items():
        paths = [p for p in paths if p]
        if paths:
            print(f"\n[+] {label} generated:")
            print("\n".join(f"    - {p}" for p in paths))

    if args.output:
        try:
            with open(args.output, "w", encoding="utf-8") as fh:
                json.dump(json_dump, fh, indent=2, ensure_ascii=False)
            print(f"\n[+] JSON results saved: {args.output}")
        except Exception as e:
            print(f"[-] Error writing JSON output: {e}")


if __name__ == "__main__":
    check_environment_and_dependencies()
    try:
        main()
    except KeyboardInterrupt:
        print("\n[!] Interrupted by user")
        sys.exit(130)
