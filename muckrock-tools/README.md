# Muckrock Toolkit

## Description
This repo provides tools for searching Muckrock FOIA (Freedom of Information Act) requests, it includes scripts for downloading data from MuckRock, generating detailed CSV files, and labeling via ML.

## Uses

### 1. Simple Search Term
- `muck-get.py`
- **Description**: script to perform searches on MuckRock's database, by matching a search string to title of request. Search is slow due to rate limiting (cannot multi thread around it).

### 2. Clone Muckrock database & search locally
- `download-muckrock-foia.py` `search-local-foia-json.py`
- **Description**: scripts to clone the MuckRock repository for fast local querying (total size <2GB at present)

### 3. County Level Search
- `get-allegheny-foias.py`, `allegheny-county-towns.txt`
- **Description**: To search for any and all requests in a certain county (e.g. Allegheny in this case) you must provide a list of all municipalities contained within the county. Muckrock stores geographic info in tiers, from Federal, State, and local level. At the local level, e.g. Pittsburgh and Allegheny County are in the same tier, with no way to determine which municipalities reside within a county (without providing it yourself).

The `get-allegheny-foias.py` script will find the jurisdiction ID for each municipality in `allegheny-county-towns.txt`, then find all completed FOIA requests for those jurisdictions.

### 4. Generate detailed FOIA data in PDAP database format 
- `generate-detailed-muckrock-csv.py`
- **Description**: Once you have a json of relevant FOIA's, run it through this script to generate a CSV that fulfills PDAP database requirements.

### 5. ML Labeling 
- `muckrock-ml-labeler.py`
- **Description**: A tool for labeling MuckRock sources via our ML models hosted on HuggingFace. This script is using the fine-url-labeler model to assign 1 of 36 record type labels. At present, muckrock url's must first be run through `html-tag-collector/collector.py`. 
