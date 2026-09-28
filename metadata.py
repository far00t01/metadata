#!/usr/bin/env python3
import sys
import os
import argparse
from urllib.parse import urljoin, urlparse
import threading
from concurrent.futures import ThreadPoolExecutor
import time
import re
from collections import defaultdict

def check_environment_and_dependencies():
    
    in_venv = hasattr(sys, 'real_prefix') or (hasattr(sys, 'base_prefix') and sys.base_prefix != sys.prefix) or os.getenv('VIRTUAL_ENV')
    
    if not in_venv:
        print("=" * 65)
        print("[!] WARNING: No active virtual environment (venv) detected.")
        print("-" * 65)
        print("    It is strongly recommended to run this tool inside a venv.")
        print("    Quick setup command:")
        print("    > python3 -m venv venv && source venv/bin/activate")
        print("=" * 65 + "\n")
    
    missing_libs = []
    
    try:
        import requests
    except ImportError:
        missing_libs.append("requests")
        
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        missing_libs.append("beautifulsoup4")
        
    try:
        import pypdf
    except ImportError:
        missing_libs.append("pypdf")
        
    if missing_libs:
        print("=" * 65)
        print("[X] CRITICAL ERROR: Missing required dependencies.")
        print("-" * 65)
        print("    Please install the missing packages by running:")
        print(f"    > pip install {' '.join(missing_libs)}")
        print("    Or: > pip install -r requirements.txt")
        print("=" * 65 + "\n")
        sys.exit(1)

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    pass

try:
    import pypdf
    PDF_SUPPORT = True
except ImportError:
    PDF_SUPPORT = False


class MetadataExtractor:
    @staticmethod
    def extract_pdf_metadata_advanced(file_content: bytes, url: str) -> dict:
        metadata = {
            'URL': url,
            'Filename': os.path.basename(urlparse(url).path),
            'FileSize': f"{len(file_content):,} bytes"
        }
        
        if not PDF_SUPPORT:
            metadata['Error'] = 'pypdf not installed'
            return metadata
            
        try:
            from io import BytesIO
            pdf_file = BytesIO(file_content)
            
            try:
                pdf_reader = pypdf.PdfReader(pdf_file)
                
                if hasattr(pdf_reader, 'metadata') and pdf_reader.metadata:
                    raw_metadata = pdf_reader.metadata
                    
                    metadata_fields = [
                        ('/Producer', 'Producer'),
                        ('/Creator', 'Creator'),
                        ('/Author', 'Author'),
                        ('/Title', 'Title'),
                        ('/Subject', 'Subject'),
                        ('/Keywords', 'Keywords'),
                        ('/CreationDate', 'CreationDate'),
                        ('/ModDate', 'ModificationDate'),
                        ('/CreatorTool', 'CreatorTool'),
                        ('/Trapped', 'Trapped'),
                    ]
                    
                    for pdf_field, normal_field in metadata_fields:
                        try:
                            if hasattr(raw_metadata, pdf_field.strip('/')):
                                value = getattr(raw_metadata, pdf_field.strip('/'), None)
                                if value and str(value).strip() and str(value).strip() != 'None':
                                    clean_value = str(value).strip()
                                    clean_value = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', clean_value)
                                    metadata[normal_field] = clean_value
                        except Exception:
                            continue
                
                try:
                    metadata['Pages'] = str(len(pdf_reader.pages))
                except Exception:
                    metadata['Pages'] = 'Unknown'
                
                try:
                    if pdf_reader.is_encrypted:
                        metadata['Encrypted'] = 'Yes'
                        common_passwords = ['', ' ']
                        for password in common_passwords:
                            try:
                                if pdf_reader.decrypt(password):
                                    metadata['Encrypted'] = f'Decrypted (password: {"empty" if password == "" else password})'
                                    break
                            except Exception:
                                continue
                except Exception:
                    pass
                
                try:
                    content_str = file_content.decode('latin-1', errors='ignore')
                    
                    patterns = {
                        'Producer': r'/Producer\s*\(([^)]+)\)',
                        'Creator': r'/Creator\s*\(([^)]+)\)',
                        'Author': r'/Author\s*\(([^)]+)\)',
                        'Title': r'/Title\s*\(([^)]+)\)',
                        'Subject': r'/Subject\s*\(([^)]+)\)',
                        'Keywords': r'/Keywords\s*\(([^)]+)\)',
                        'CreationDate': r'/CreationDate\s*\(([^)]+)\)',
                        'ModDate': r'/ModDate\s*\(([^)]+)\)',
                    }
                    
                    for field, pattern in patterns.items():
                        if field not in metadata:
                            matches = re.findall(pattern, content_str)
                            if matches:
                                clean_value = matches[0].strip()
                                if clean_value and clean_value != 'None':
                                    clean_value = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', clean_value)
                                    metadata[field] = clean_value
                except Exception:
                    pass
                
                try:
                    if hasattr(pdf_reader, 'trailer'):
                        trailer = pdf_reader.trailer
                        if '/Info' in trailer:
                            info_dict = trailer['/Info']
                            if hasattr(info_dict, 'items'):
                                for key, value in info_dict.items():
                                    if str(key).startswith('/'):
                                        field_name = str(key)[1:]
                                        if field_name not in metadata:
                                            clean_value = str(value).strip()
                                            if clean_value and clean_value != 'None':
                                                clean_value = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', clean_value)
                                                metadata[field_name] = clean_value
                except Exception:
                    pass
                
                return metadata
                
            except Exception as e:
                metadata['Error'] = f'PDF parsing failed: {str(e)}'
                return metadata
                
        except Exception as e:
            metadata['Error'] = f'PDF processing failed: {str(e)}'
            return metadata

    @staticmethod
    def extract_zip_metadata(file_content: bytes, url: str) -> dict:
        metadata = {
            'URL': url,
            'Filename': os.path.basename(urlparse(url).path),
            'FileSize': f"{len(file_content):,} bytes",
            'Type': 'ZIP Archive'
        }
        
        try:
            import zipfile
            from io import BytesIO
            
            zip_file = BytesIO(file_content)
            with zipfile.ZipFile(zip_file, 'r') as zf:
                file_list = zf.namelist()
                metadata['FilesInArchive'] = str(len(file_list))
                if file_list:
                    metadata['SampleFiles'] = ', '.join(file_list[:3]) + ('...' if len(file_list) > 3 else '')
                    total_size = sum(zf.getinfo(f).file_size for f in file_list)
                    metadata['TotalUncompressedSize'] = f"{total_size:,} bytes"
        except Exception as e:
            metadata['ZipInfo'] = f'Limited info: {str(e)}'
        
        return metadata

    @staticmethod
    def extract_office_metadata(file_content: bytes, url: str, extension: str) -> dict:
        file_type = {
            '.doc': 'Word Document',
            '.docx': 'Word Document',
            '.xls': 'Excel Spreadsheet',
            '.xlsx': 'Excel Spreadsheet',
            '.ppt': 'PowerPoint Presentation',
            '.pptx': 'PowerPoint Presentation'
        }.get(extension.lower(), 'Office Document')
        
        metadata = {
            'URL': url,
            'Filename': os.path.basename(urlparse(url).path),
            'FileSize': f"{len(file_content):,} bytes",
            'Type': file_type
        }
        
        try:
            content_str = file_content.decode('latin-1', errors='ignore')
            
            office_patterns = {
                'Author': r'Author[^A-Za-z]*([A-Za-z0-9\s\.@]+)',
                'Title': r'Title[^A-Za-z]*([A-Za-z0-9\s\.]+)',
                'Subject': r'Subject[^A-Za-z]*([A-Za-z0-9\s\.]+)',
                'Company': r'Company[^A-Za-z]*([A-Za-z0-9\s\.]+)',
            }
            
            for field, pattern in office_patterns.items():
                matches = re.findall(pattern, content_str, re.IGNORECASE)
                if matches:
                    clean_value = matches[0].strip()
                    if clean_value and len(clean_value) > 1:
                        metadata[field] = clean_value
        except Exception:
            pass
        
        return metadata

    @staticmethod
    def extract_metadata(file_content: bytes, url: str, extension: str) -> dict:
        extension = extension.lower()
        
        try:
            if extension == '.pdf':
                return MetadataExtractor.extract_pdf_metadata_advanced(file_content, url)
            elif extension == '.zip':
                return MetadataExtractor.extract_zip_metadata(file_content, url)
            elif extension in ['.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx']:
                return MetadataExtractor.extract_office_metadata(file_content, url, extension)
            else:
                return {
                    'URL': url,
                    'Filename': os.path.basename(urlparse(url).path),
                    'FileSize': f"{len(file_content):,} bytes",
                    'Type': 'File'
                }
        except Exception as e:
            return {
                'URL': url,
                'Filename': os.path.basename(urlparse(url).path),
                'FileSize': f"{len(file_content):,} bytes",
                'Error': f'Metadata extraction failed: {str(e)}'
            }


class FileFinder:
    def __init__(self, max_threads=10, timeout=30, extract_metadata_flag=True, max_file_size_mb=50):
        self.max_threads = max_threads
        self.timeout = timeout
        self.extract_metadata = extract_metadata_flag
        self.max_file_size = max_file_size_mb * 1024 * 1024
        self.visited_urls = set()
        self.found_files = set()
        self.files_with_metadata = {}
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        })
        self.lock = threading.Lock()
        self.metadata_extractor = MetadataExtractor()
        
        self.target_extensions = {
            '.pdf': 'PDF',
            '.doc': 'Word',
            '.docx': 'Word',
            '.xls': 'Excel',
            '.xlsx': 'Excel',
            '.ppt': 'PowerPoint',
            '.pptx': 'PowerPoint',
            '.zip': 'ZIP',
            '.rar': 'RAR'
        }

        self.target_fields = [
            'Producer',
            'Creator',
            'Title', 
            'Author',
            'Pages',
            'FileSize'
        ]

    def is_same_domain(self, url, base_domain):
        try:
            parsed_url = urlparse(url)
            parsed_base = urlparse(base_domain)
            
            base_domain_main = '.'.join(parsed_base.netloc.split('.')[-2:])
            url_domain_main = '.'.join(parsed_url.netloc.split('.')[-2:])
            
            return base_domain_main == url_domain_main
        except Exception:
            return False

    def download_and_extract_metadata(self, url: str, extension: str) -> dict:
        try:
            filename = os.path.basename(urlparse(url).path)
            print(f"    [+] Downloading: {filename}")
            
            try:
                head_response = self.session.head(url, timeout=10, verify=False, allow_redirects=True)
                content_length = head_response.headers.get('content-length')
                if content_length:
                    file_size = int(content_length)
                    if file_size > self.max_file_size:
                        return {
                            'URL': url,
                            'Filename': filename,
                            'Error': f'File too large for metadata extraction ({file_size:,} bytes > {self.max_file_size // (1024*1024)}MB)'
                        }
            except Exception:
                pass
            
            response = self.session.get(url, timeout=self.timeout, verify=False, stream=True)
            response.raise_for_status()
            
            content = b''
            for chunk in response.iter_content(chunk_size=8192):
                content += chunk
                if len(content) > self.max_file_size:
                    return {
                        'URL': url,
                        'Filename': filename,
                        'Error': f'File too large for metadata extraction (>{self.max_file_size // (1024*1024)}MB)'
                    }
            
            if len(content) == 0:
                return {
                    'URL': url,
                    'Filename': filename,
                    'Error': 'Empty file'
                }
            
            print(f"    [✓] Downloaded: {filename} ({len(content):,} bytes)")
            
            metadata = self.metadata_extractor.extract_metadata(content, url, extension)
            return metadata
            
        except Exception as e:
            error_msg = f'Download failed: {str(e)}'
            print(f"    [-] Error: {error_msg}")
            return {
                'URL': url,
                'Filename': os.path.basename(urlparse(url).path),
                'Error': error_msg
            }

    def extract_file_links(self, html_content, base_url):
        soup = BeautifulSoup(html_content, 'html.parser')
        file_links = []
        
        for link in soup.find_all('a', href=True):
            href = link['href']
            for ext in self.target_extensions:
                if href.lower().endswith(ext):
                    full_url = urljoin(base_url, href)
                    file_links.append((full_url, ext))
                    print(f"    [DEBUG] [Enlace Archivo Detectado] Extensión '{ext}' encontrada en <a>: {full_url}")

        for tag in soup.find_all(['iframe', 'embed', 'object', 'script']):
            src = tag.get('src') or tag.get('data')
            if src:
                for ext in self.target_extensions:
                    if src.lower().endswith(ext):
                        full_url = urljoin(base_url, src)
                        file_links.append((full_url, ext))
                        print(f"    [DEBUG] [Enlace Archivo Detectado] Extensión '{ext}' encontrada en <{tag.name}>: {full_url}")
                        
        print(f"    [DEBUG] Total de archivos de interés encontrados en la página: {len(file_links)}")
        return file_links

    def extract_all_links(self, html_content, base_url, target_domain):
        soup = BeautifulSoup(html_content, 'html.parser')
        links = set()
        
        for link in soup.find_all('a', href=True):
            href = link['href']
            full_url = urljoin(base_url, href)
            
            if (self.is_same_domain(full_url, target_domain) and 
                not any(full_url.lower().endswith(ext) for ext in self.target_extensions) and
                not full_url.startswith(('javascript:', 'mailto:', 'tel:')) and
                '#' not in full_url):
                links.add(full_url)
        
        return links

    def crawl_page(self, url, target_domain):
        if url in self.visited_urls:
            return set(), set()
        
        with self.lock:
            self.visited_urls.add(url)
        
        try:
            print(f"[+] Analyzing: {url}")
            response = self.session.get(url, timeout=self.timeout, verify=False)
            response.raise_for_status()
            
            files_found = self.extract_file_links(response.content, url)
            links_found = self.extract_all_links(response.content, url, target_domain)
            
            return set(files_found), links_found
            
        except Exception as e:
            print(f"[-] Error analyzing {url}: {e}")
            return set(), set()

    def find_files(self, start_url, max_depth=2):
        target_domain = start_url
        to_visit = [(start_url, 0)]
        all_files = set()
        
        with ThreadPoolExecutor(max_workers=self.max_threads) as executor:
            while to_visit:
                current_batch = []
                futures = []
                
                while to_visit and len(current_batch) < self.max_threads:
                    url, depth = to_visit.pop(0)
                    if url not in self.visited_urls:
                        current_batch.append((url, depth))
                
                for url, depth in current_batch:
                    future = executor.submit(self.crawl_page, url, target_domain)
                    futures.append((future, depth))
                
                for future, depth in futures:
                    try:
                        files, links = future.result(timeout=self.timeout + 5)
                        all_files.update(files)
                        
                        if depth < max_depth:
                            for link in links:
                                if link not in self.visited_urls:
                                    to_visit.append((link, depth + 1))
                    
                    except Exception as e:
                        print(f"[-] Error processing result: {e}")
                
                time.sleep(0.1)
        
        if self.extract_metadata and all_files:
            print(f"\n[+] Extracting metadata from {len(all_files)} files...")
            metadata_futures = {}
            
            with ThreadPoolExecutor(max_workers=4) as metadata_executor:
                for file_url, extension in all_files:
                    future = metadata_executor.submit(
                        self.download_and_extract_metadata, file_url, extension
                    )
                    metadata_futures[future] = file_url
                
                completed = 0
                total = len(metadata_futures)
                
                for future in metadata_futures:
                    try:
                        file_url = metadata_futures[future]
                        metadata = future.result(timeout=60)
                        self.files_with_metadata[file_url] = metadata
                        completed += 1
                        
                        interesting = self._get_filtered_metadata(metadata)
                        if any(value != 'Unidentified' for key, value in interesting.items() if key != 'FileSize'):
                            print(f"    [✓] Metadata found: {completed}/{total} - {os.path.basename(urlparse(file_url).path)}")
                        else:
                            print(f"    [✓] Basic metadata: {completed}/{total} - {os.path.basename(urlparse(file_url).path)}")
                            
                    except Exception as e:
                        print(f"    [-] Error extracting metadata: {e}")
        
        return all_files

    def _get_output_dir(self, domain):
        if not domain.startswith(('http://', 'https://')):
            domain = f"http://{domain}"
        parsed = urlparse(domain)
        netloc = parsed.netloc
        if netloc.startswith('www.'):
            netloc = netloc[4:]
        folder_name = f"{netloc}_results"
        os.makedirs(folder_name, exist_ok=True)
        return folder_name

    def generate_summary_file(self, results, domain):
        output_dir = self._get_output_dir(domain)
        domain_name = urlparse(domain if '://' in domain else f'http://{domain}').netloc.replace(':', '_')
        filename = os.path.join(output_dir, f"{domain_name}_idem-meta-summary.md")
        
        try:
            with open(filename, 'w', encoding='utf-8') as f:
                f.write(f"# File Metadata Summary - {domain}\n\n")
                f.write(f"**Analyzed Domain:** {domain}\n")
                f.write(f"**Analysis Date:** {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")
                
                total_files = 0
                files_with_metadata = 0
                files_with_interesting_metadata = 0
                
                for current_domain, files in results.items():
                    if current_domain == domain and files:
                        files_by_type = {}
                        for file_url, ext in files:
                            file_type = self.target_extensions.get(ext, 'Unknown')
                            if file_type not in files_by_type:
                                files_by_type[file_type] = []
                            files_by_type[file_type].append((file_url, ext))
                        
                        for file_type, file_list in sorted(files_by_type.items()):
                            f.write(f"## {file_type} ({len(file_list)} files)\n\n")
                            
                            for url, ext in sorted(file_list):
                                total_files += 1
                                filename_display = os.path.basename(urlparse(url).path)
                                
                                f.write(f"### File: {filename_display}\n\n")
                                f.write(f"**Link:** [{filename_display}]({url})\n\n")
                                
                                if url in self.files_with_metadata:
                                    files_with_metadata += 1
                                    metadata = self.files_with_metadata[url]
                                    filtered_metadata = self._get_filtered_metadata(metadata)
                                    
                                    if any(value != 'Unidentified' for key, value in filtered_metadata.items() if key != 'FileSize'):
                                        files_with_interesting_metadata += 1
                                        f.write("| Field | Value |\n")
                                        f.write("|-------|-------|\n")
                                        
                                        for key, value in filtered_metadata.items():
                                            clean_value = str(value).replace('|', '&#124;')
                                            f.write(f"| {key} | {clean_value} |\n")
                                    else:
                                        f.write("*No interesting metadata found*\n")
                                        if 'Error' in metadata:
                                            f.write(f"*Error: {metadata['Error']}*\n")
                                else:
                                    f.write("*Could not extract metadata*\n")
                                
                                f.write("\n---\n\n")
            
            print(f"[+] Summary file generated: {filename}")
            return filename
            
        except Exception as e:
            print(f"[-] Error generating summary file: {e}")
            return None

    def generate_urls_txt_file(self, results, domain):
        output_dir = self._get_output_dir(domain)
        domain_name = urlparse(domain if '://' in domain else f'http://{domain}').netloc.replace(':', '_')
        filename = os.path.join(output_dir, f"{domain_name}_url-metadata.txt")
        
        try:
            with open(filename, 'w', encoding='utf-8') as f:
                for current_domain, files in results.items():
                    if current_domain == domain and files:
                        for file_url, ext in sorted(files):
                            f.write(f"{file_url}\n")
            
            print(f"[+] URLs text file generated: {filename}")
            return filename
            
        except Exception as e:
            print(f"[-] Error generating URLs text file: {e}")
            return None

    def generate_executive_summary(self, results, domain):
        output_dir = self._get_output_dir(domain)
        domain_name = urlparse(domain if '://' in domain else f'http://{domain}').netloc.replace(':', '_')
        filename = os.path.join(output_dir, f"{domain_name}_meta_executive-summary.md")
        
        try:
            with open(filename, 'w', encoding='utf-8') as f:
                f.write(f"# Metadata Executive Summary - {domain}\n\n")
                f.write(f"**Analyzed Domain:** {domain}\n")
                f.write(f"**Analysis Date:** {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")
                
                total_files = 0
                files_with_metadata = 0
                files_by_type = defaultdict(int)
                
                for current_domain, files in results.items():
                    if current_domain == domain and files:
                        total_files = len(files)
                        files_with_metadata = sum(1 for url, _ in files if url in self.files_with_metadata)
                        
                        for file_url, ext in files:
                            file_type = self.target_extensions.get(ext, 'Unknown')
                            files_by_type[file_type] += 1
                
                f.write("## General Statistics\n\n")
                f.write("| Metric | Value |\n")
                f.write("|--------|-------|\n")
                f.write(f"| Total files found | {total_files} |\n")
                f.write(f"| Files with metadata extracted | {files_with_metadata} |\n")
                f.write(f"| Different file types | {len(files_by_type)} |\n\n")
                
                if files_by_type:
                    f.write("## Distribution by File Type\n\n")
                    f.write("| Type | Count |\n")
                    f.write("|------|-------|\n")
                    for file_type, count in sorted(files_by_type.items()):
                        f.write(f"| {file_type} | {count} |\n")
                    f.write("\n")
                
                f.write("## Consolidated Metadata Summary\n\n")
                
                unique_values = defaultdict(set)
                file_count_by_value = defaultdict(lambda: defaultdict(int))
                
                excluded_fields = ['Pages', 'FileSize']
                
                for file_url, _ in results.get(domain, []):
                    if file_url in self.files_with_metadata:
                        metadata = self.files_with_metadata[file_url]
                        filtered_metadata = self._get_filtered_metadata(metadata)
                        
                        for field, value in filtered_metadata.items():
                            if value != 'Unidentified' and field not in excluded_fields:
                                unique_values[field].add(value)
                                file_count_by_value[field][value] += 1
                
                for field in self.target_fields:
                    if field not in excluded_fields and field in unique_values and unique_values[field]:
                        f.write(f"### {field}\n\n")
                        f.write("| Value | Files |\n")
                        f.write("|-------|-------|\n")
                        
                        sorted_values = sorted(
                            file_count_by_value[field].items(), 
                            key=lambda x: x[1], 
                            reverse=True
                        )
                        
                        for value, count in sorted_values:
                            clean_value = str(value).replace('|', '&#124;')
                            f.write(f"| {clean_value} | {count} |\n")
                        f.write("\n")
                
                if 'Author' in unique_values and unique_values['Author']:
                    f.write("## Files by Author\n\n")
                    f.write("| Author | Files | Examples |\n")
                    f.write("|--------|-------|----------|\n")
                    
                    author_files = defaultdict(list)
                    for file_url, _ in results.get(domain, []):
                        if file_url in self.files_with_metadata:
                            metadata = self.files_with_metadata[file_url]
                            author = metadata.get('Author', 'Unidentified')
                            if author != 'Unidentified':
                                filename_display = os.path.basename(urlparse(file_url).path)
                                author_files[author].append(filename_display)
                    
                    for author, files in sorted(author_files.items(), key=lambda x: len(x[1]), reverse=True):
                        sample_files = ', '.join(files[:3]) + ('...' if len(files) > 3 else '')
                        clean_author = str(author).replace('|', '&#124;')
                        f.write(f"| {clean_author} | {len(files)} | {sample_files} |\n")
                    f.write("\n")
                
                if 'Creator' in unique_values and unique_values['Creator']:
                    f.write("## Creation Tools Used\n\n")
                    f.write("| Tool | Files |\n")
                    f.write("|------|-------|\n")
                    
                    creator_count = file_count_by_value['Creator']
                    for creator, count in sorted(creator_count.items(), key=lambda x: x[1], reverse=True):
                        clean_creator = str(creator).replace('|', '&#124;')
                        f.write(f"| {clean_creator} | {count} |\n")
                    f.write("\n")
            
            print(f"[+] Executive summary generated: {filename}")
            return filename
            
        except Exception as e:
            print(f"[-] Error generating executive summary: {e}")
            return None

    def _get_filtered_metadata(self, metadata: dict) -> dict:
        filtered_metadata = {}
        
        for field in self.target_fields:
            if field in metadata and metadata[field] and str(metadata[field]).strip():
                clean_value = str(metadata[field]).strip()
                
                clean_value = re.sub(r'^[\xfe\xff\ufffe\ufeff]+', '', clean_value)
                clean_value = clean_value.replace('\u00fe\u00ff', '').replace('\xff\xfe', '')
                
                clean_value = re.sub(r'[\x00-\x1f\x7f-\x9f]', '', clean_value)
                
                clean_value = clean_value.strip()
                filtered_metadata[field] = clean_value if clean_value else 'Unidentified'
            else:
                filtered_metadata[field] = 'Unidentified'
        
        return filtered_metadata


def check_dependencies():
    missing_deps = []
    
    if not PDF_SUPPORT:
        missing_deps.append("pypdf")
    
    if missing_deps:
        print("[-] Missing dependencies for metadata extraction:")
        for dep in missing_deps:
            print(f"    - {dep}")
        print("\n[+] Install using: pip install pypdf")
        return False
    
    return True


def main():
    banner = r"""
                 _            _       _         
                | |          | |     | |        
  _ __ ___   ___| |_ __ _  __| | __ _| |_ __ _  
 | '_ ` _ \ / _ \ __/ _` |/ _` |/ _` | __/ _` |
 | | | | | |  __/ || (_| | (_| | (_| | || (_| |
 |_| |_| |_|\___|\__\__,_|\__,_|\__,_|\__\__,_| v1.1
         Web File Recon & Metadata Extractor
                by @far00t01
    """
    print(banner)

    custom_usage = "python3 metadata.py [domain].com"
    
    parser = argparse.ArgumentParser(
        usage=custom_usage,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    parser.add_argument('domain', nargs='?', help='Domain to analyze (e.g., domain.com)')
    parser.add_argument('-f', '--file', help='File with a list of domains (one per line)')
    parser.add_argument('-t', '--threads', type=int, default=3, help='Maximum number of threads (default: 3)')
    parser.add_argument('-d', '--depth', type=int, default=2, help='Maximum crawling depth (default: 2)')
    parser.add_argument('-o', '--output', help='Output file to save detailed results')
    parser.add_argument('--no-summary', action='store_true', help='Do not generate per-domain summary files')
    parser.add_argument('--no-metadata', action='store_true', help='Do not extract metadata (find files only)')
    parser.add_argument('--metadata-timeout', type=int, default=45, help='Timeout for metadata downloads (default: 45)')
    parser.add_argument('--max-file-size', type=int, default=50, help='Maximum file size in MB (default: 50)')
    parser.add_argument('--executive-only', action='store_true', help='Generate executive summary only')
    
    args = parser.parse_args()
    
    if not args.domain and not args.file:
        parser.print_help()
        sys.exit(1)
    
    if not args.no_metadata and not check_dependencies():
        print("[!] Continuing without metadata extraction...")
        args.no_metadata = True
    
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    
    finder = FileFinder(
        max_threads=args.threads, 
        timeout=args.metadata_timeout,
        extract_metadata_flag=not args.no_metadata,
        max_file_size_mb=args.max_file_size
    )
    
    domains = []
    if args.domain:
        if not args.domain.startswith(('http://', 'https://')):
            domains.append(f"https://{args.domain}")
        else:
            domains.append(args.domain)
    
    if args.file:
        try:
            with open(args.file, 'r') as f:
                for line in f:
                    domain = line.strip()
                    if domain:
                        if not domain.startswith(('http://', 'https://')):
                            domain = f"https://{domain}"
                        domains.append(domain)
        except FileNotFoundError:
            print(f"[-] Error: File {args.file} not found")
            sys.exit(1)
    
    all_results = {}
    
    print("[+] Target extensions searched (files with metadata):")
    for ext, file_type in finder.target_extensions.items():
        print(f"    - {ext} ({file_type})")
    print(f"\n[+] Metadata extraction: {'ACTIVATED' if not args.no_metadata else 'DEACTIVATED'}")
    print(f"[+] Displayed fields: {', '.join(finder.target_fields)}")
    print(f"[+] Maximum file size: {args.max_file_size}MB")
    print()
    
    summary_files = []
    executive_files = []
    url_txt_files = []
    
    for domain in domains:
        print(f"\n[+] Starting analysis of: {domain}")
        
        finder.visited_urls.clear()
        finder.files_with_metadata.clear()
        files = finder.find_files(domain, max_depth=args.depth)
        all_results[domain] = files
        
        print(f"\n[+] Results for {domain}:")
        print("=" * 70)
        if files:
            files_by_type = {}
            for file_url, ext in files:
                file_type = finder.target_extensions.get(ext, 'Unknown')
                if file_type not in files_by_type:
                    files_by_type[file_type] = []
                files_by_type[file_type].append(file_url)
            
            for file_type, urls in sorted(files_by_type.items()):
                print(f"\n{file_type} ({len(urls)}):")
                for url in sorted(urls)[:3]:
                    print(f"  {url}")
                if len(urls) > 3:
                    print(f"  ... and {len(urls) - 3} more")
        else:
            print("No files found")
        print("=" * 70)
        
        if not args.no_summary and not args.executive_only:
            summary_file = finder.generate_summary_file({domain: files}, domain)
            if summary_file:
                summary_files.append(summary_file)

        url_txt_file = finder.generate_urls_txt_file({domain: files}, domain)
        if url_txt_file:
            url_txt_files.append(url_txt_file)
        
        executive_file = finder.generate_executive_summary({domain: files}, domain)
        if executive_file:
            executive_files.append(executive_file)
    
    if summary_files:
        print(f"\n[+] Summary files generated:")
        print('\n'.join([f"    - {file}" for file in summary_files]))
    
    if url_txt_files:
        print(f"\n[+] URL list files generated:")
        print('\n'.join([f"    - {file}" for file in url_txt_files]))

    if executive_files:
        print(f"\n[+] Executive summaries generated:")
        print('\n'.join([f"    - {file}" for file in executive_files]))


if __name__ == "__main__":
    check_environment_and_dependencies()
    main()
