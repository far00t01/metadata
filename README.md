<div align="center">

# metadata 🗃️

### *Web Application File Reconnaissance & Metadata Extractor*

[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Library: BeautifulSoup4 & PyPDF](https://img.shields.io/badge/Libraries-BS4%20%7C%20PyPDF-red.svg)](https://pypi.org/)
[![Platform: Cross-platform](https://img.shields.io/badge/Platform-Linux%20%7C%20macOS%20%7C%20Windows-lightgrey.svg)](https://www.python.org/)

</div>

<br>

**metadata** is a powerful security auditing and Open Source Intelligence (OSINT) tool designed for penetration testers, security analysts, and bug hunters. It recursively crawls target domains to unearth hidden or indexed files (such as PDFs, Word documents, Excel spreadsheets, and ZIP archives), extracts deep metadata embedded within them, and automatically organizes structured executive and technical reports.

## The Concept Behind the Tool
Web applications frequently leak sensitive internal information through documents uploaded to public directories. Metadata fields (such as internal author names, software creation tools, user accounts, and structural folder paths) often provide crucial intelligence for social engineering or attack surface expansion. **metadata** automates the tedious process of discovering these files, downloading them securely, sanitizing metadata artifacts, and compiling comprehensive summaries.

## Disclaimer
> **⚠️ WARNING:** This tool is provided strictly for educational purposes, quality assurance (QA), and authorized security assessments. Unauthorized scanning, file harvesting, or extracting metadata from systems without explicit, prior written consent from the target owners is illegal. The author assumes no liability and is not responsible for any misuse or damage caused by this software. Use responsibly and ethically.

## Why Use metadata?
Metadata bridges the gap between basic web scraping and advanced metadata forensics by offering:

**Automated Deep Crawling**
- Recursively navigates same-domain links up to configurable depths using a high-performance multithreaded engine.
- Targets specific file extensions (`.pdf`, `.doc`, `.docx`, `.xls`, `.xlsx`, `.ppt`, `.pptx`, `.zip`, `.rar`) while ignoring external distractions.

**Advanced Metadata Forensics**
- Extracts hidden details from **PDFs** (Producer, Creator, Author, Title, Subject, Keywords, Creation Date, Page Counts, and even encrypted/password recovery checks).
- Extracts structural information from **ZIP archives** (file lists, uncompressed sizes) and **Microsoft Office documents** (internal author signatures, company names, and application tools).
- Automatically sanitizes encoding artifacts (such as UTF-16 BOMs and `þÿ` prefixes) for clean report rendering.

**Structured Automated Reporting**
- Automatically organizes outputs into domain-specific directories (`domain.com_results/`).
- Generates clean plain-text URL lists (`_url-metadatos.txt`) ideal for chaining with other security tools.
- Compiles fully formatted Markdown reports containing detailed file summaries (`_idem-meta-summary.md`) and high-level analytical metrics (`_meta_executive-summary.md`).

## Main Features
- **Multithreaded Performance:** Fast asynchronous processing using Python's `ThreadPoolExecutor`.
- **Safe Resource Management:** Built-in file size limits (`--max-file-size`) and download timeouts to prevent memory exhaustion on large binaries.
- **Customizable Execution:** Adjust thread counts, crawling depth, timeouts, or omit metadata extraction to act as a pure file finder.
- **Clean Terminal UI:** Structured execution prompts with real-time progress indicators.

## Installation
Ensure you have Python 3.8 or higher installed, then clone the repository and install dependencies:

```bash
git clone https://github.com/far00t01/metadata.git

// Create and activate a virtual environment
python3 -m venv venv && source venv/bin/activate

// Install required dependencies
pip install -r requirements.txt
```

## Usage
To analyze a target domain and automatically generate your executive reports and URL lists, run the main script:
```bash
python3 -m venv venv && source venv/bin/activate
python3 metadata.py

positional arguments:
  domain                Domain to analyze (e.g., domain.com)

options:
  -h, --help            show this help message and exit
  -f FILE, --file FILE  File with a list of domains (one per line)
  -t THREADS, --threads THREADS
                        Maximum number of threads (default: 3)
  -d DEPTH, --depth DEPTH
                        Maximum crawling depth (default: 2)
  -o OUTPUT, --output OUTPUT
                        Output file to save detailed results
  --no-summary          Do not generate per-domain summary files
  --no-metadata         Do not extract metadata (find files only)
  --metadata-timeout METADATA_TIMEOUT
                        Timeout for metadata downloads (default: 45)
  --max-file-size MAX_FILE_SIZE
                        Maximum file size in MB (default: 50)
  --executive-only      Generate executive summary only
```

### Recommended Defensive Mitigation (For Developers)
If your web application publishes documents, consider implementing the following security practices to minimize information disclosure:
- Implement automated pre-upload or post-processing pipelines that strip sensitive metadata (author names, internal usernames, software versions, and local paths) from all published documents.
- Ensure documents containing internal corporate insights, draft policies, or user data are stored behind authentication barriers rather than public web-accessible directories (/wp-content/uploads/, /assets/docs/).
- Ensure that web servers (Nginx/Apache) have directory indexing explicitly turned off (autoindex off or Options -Indexes) to prevent attackers from browsing unlinked file folders.

## Author
_Developed and maintained by: Fabián Rosales_
- **Medium:** [@far00t01](https://medium.com/@far00t01/)
- **GitHub:** [far00t01](https://github.com/far00t01)
- **LinkedIn:** [frosalesr](https://linkedin.com/in/frosalesr)


