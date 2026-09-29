<div align="center">

# metadata 🗃️
<img width="1156" height="656" alt="image" src="https://github.com/user-attachments/assets/4c3a1052-484e-4435-8d83-ba281ef98401" />


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

## Architecture
**metadata** combines multithreaded web reconnaissance with advanced metadata forensics:

```mermaid
graph TD
    %% Estilos de nodos
    classDef startEnd fill:#1f2428,stroke:#58a6ff,stroke-width:2px,color:#fff;
    classDef process fill:#2d333b,stroke:#444c56,stroke-width:2px,color:#fff;
    classDef decision fill:#373e47,stroke:#f0883e,stroke-width:2px,color:#fff;
    classDef output fill:#111a21,stroke:#2ea043,stroke-width:2px,color:#fff;

    %% Flujo principal
    Start([User / CLI Input]) --> CheckEnv[Environment & Dependency Check]:::startEnd
    CheckEnv --> Init[Initialize FileFinder & Session]:::process
    
    Init --> Crawl[Recursive Web Crawling<br/>ThreadPoolExecutor & BeautifulSoup]:::process
    Crawl --> SameDomain{Same Domain?}:::decision
    
    SameDomain -- No --> Discard[Skip / External Link]:::process
    SameDomain -- Yes --> ExtractLinks[Extract Target Files<br/>PDF, Office, ZIP]:::process
    
    ExtractLinks --> FoundFiles{Files Found?}:::decision
    FoundFiles -- No --> EndNoFiles([End: No Files]):::startEnd
    FoundFiles -- Yes --> DL[Download & Size Check<br/>Stream Chunking & Max Size]:::process

    DL --> MetaEngine[Metadata Extractor Engine]:::process
    
    MetaEngine --> PDFExt[PDF Parser: pypdf & Regex<br/>Author, Creator, Dates, Pages]:::process
    MetaEngine --> OfficeExt[Office/ZIP Parser<br/>Internal Stats & Archives]:::process

    PDFExt --> Merge[Consolidate & Sanitize Metadata<br/>Clean UTF-16 BOMs & Artifacts]:::merge
    OfficeExt --> Merge

    Merge --> Reports[Report Generation Module]:::output

    Reports --> R1[Markdown Summary<br/>_idem-meta-summary.md]:::output
    Reports --> R2[Plain Text URLs<br/>_url-metadatos.txt]:::output
    Reports --> R3[Executive Summary<br/>_meta_executive-summary.md]:::output
```

## Main Features
- Fast asynchronous processing using Python's `ThreadPoolExecutor`.
- Built-in file size limits (`--max-file-size`) and download timeouts to prevent memory exhaustion on large binaries.
- Adjust thread counts, crawling depth, timeouts, or omit metadata extraction to act as a pure file finder.
- Structured execution prompts with real-time progress indicators.

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
python3 metadata.py
```
<img width="1088" height="766" alt="image" src="https://github.com/user-attachments/assets/d421a4b7-7b4b-4ee7-bbd5-6732491024da" />
<br>
<br>

```bash
python3 metadata.py d@main-example.com
```
<img width="1605" height="464" alt="image" src="https://github.com/user-attachments/assets/c4401261-1aa7-487f-9d88-e8720a61e000" />


## Results
The tool generates structured, clear, and comprehensive reports categorized into technical details and executive summaries to facilitate findings analysis.

Real-time console tracking during the crawling and metadata extraction stages, displaying progress bars, discovered files, and immediate status metrics.
<br>
<br>
<img width="1283" height="328" alt="image" src="https://github.com/user-attachments/assets/808cddc1-311a-43c1-8ba0-42c68a62decf" />

### Technical Report Section
Detailed breakdown of the analyzed documents and their extracted metadata parameters.
<br>
<br>
<img width="1851" height="823" alt="image" src="https://github.com/user-attachments/assets/114ff504-26f7-41b2-9f65-49e751275972" />

### Executive Summary Section
High-level overview of the discovery process and high-impact findings.
<br>
<br>
<img width="1577" height="998" alt="image" src="https://github.com/user-attachments/assets/607ae01a-1aed-41e4-8939-27de90b6f45a" />
<br>
<br>
<img width="1848" height="994" alt="image" src="https://github.com/user-attachments/assets/095a0633-01cf-47a7-bc20-769f9e3b5159" />

### URLs Text File
A clean, structured plain-text file containing the direct URLs of all files identified during the recursive web crawling phase. This inventory serves as a reliable reference list for manual auditing, secondary downloading, or feeding into other security assessment tools.
<br>
<br>
<img width="1591" height="632" alt="image" src="https://github.com/user-attachments/assets/c18b28dd-6e26-459e-814b-ea07301676fa" />


### Mitigation
If your web application publishes documents, consider implementing the following security practices to minimize information disclosure:
- Implement automated pre-upload or post-processing pipelines that strip sensitive metadata (author names, internal usernames, software versions, and local paths) from all published documents.
- Ensure documents containing internal corporate insights, draft policies, or user data are stored behind authentication barriers rather than public web-accessible directories (/wp-content/uploads/, /assets/docs/).
- Ensure that web servers (Nginx/Apache) have directory indexing explicitly turned off (autoindex off or Options -Indexes) to prevent attackers from browsing unlinked file folders.

## Author
_Developed and maintained by: Fabián Rosales_
- **Medium:** [@far00t01](https://medium.com/@far00t01/)
- **GitHub:** [far00t01](https://github.com/far00t01)
- **LinkedIn:** [frosalesr](https://linkedin.com/in/frosalesr)

#### AI Collaboration
This tool and its documentation have been iteratively developed, refined, and optimized in collaboration with **Gemini AI**, Google's advanced personal AI collaborator, ensuring clean architecture, robust error handling, and professional reporting standards.

