# NYC Datasets + LLM Research

## 1. NYC-Specific Open Datasets

### NYC Open Data Portal (opendata.cityofnewyork.us)
- **2,700+ datasets** across 80+ city agencies
- Free, publicly accessible, many with APIs and bulk CSV/JSON downloads
- Data Clinic's "scout" tool helps find related datasets

---

### A. 311 Service Requests (The Crown Jewel)
- **Source:** https://data.cityofnewyork.us/Social-Services/311-Service-Requests-from-2020-to-Present/erm2-nwe9
- **Size:** 24+ million rows, updated daily
- **Fields:** Complaint type, descriptor, location (lat/long, zip, borough), agency, resolution description, created/closed dates, free-text descriptions
- **Why it's interesting:**
  - Contains **free-text complaint descriptions** -- rich unstructured text ripe for LLM analysis
  - 343 resolution description variants that reduce to ~40 unique paragraphs (patterns in bureaucratic language)
  - Noise complaints alone: ~220,000/year (602/day)
  - Strong predictor relationships: description text predicts responding agency
  - **Gentrification signal:** BuzzFeed analysis found dramatic increases in quality-of-life complaints in gentrifying blocks (Harlem, Bushwick)
  - **Racial disparities:** NYC Comptroller audit found racial gaps in building code enforcement triggered by 311 complaints
  - New in 2025: 311 Resolution Satisfaction Survey (RSAT) data added

### B. NYC Taxi & Rideshare (TLC Trip Records)
- **Source:** https://www.nyc.gov/site/tlc/about/tlc-trip-record-data.page (also on AWS Registry)
- **Size:** 1.1+ billion trips historically; ~60M rows/year; monthly Parquet files back to 2009
- **Fields:** Pickup/dropoff times and zones, fare amount, tip, payment type, trip distance, passenger count
- **Why it's interesting:**
  - February 2017: rideshare collectively surpassed yellow+green taxis for the first time
  - Overwhelming Manhattan concentration
  - Day-of-week patterns (Sun/Mon/Tue lowest; Thu/Fri/Sat highest)
  - Cash vs. credit vs. app payment trend shifts
  - COVID impact analysis: dramatic ridership changes
  - Todd Schneider's 1.1 billion trip analysis is the gold standard reference

### C. NYPD Crime Data
- **Source:** https://data.cityofnewyork.us/Public-Safety/NYPD-Complaint-Data-Historic/qgea-i56i
- **Historic complaints** from 2006+, current year-to-date updated separately
- **Fields:** Offense description, classification (felony/misdemeanor/violation), location, precinct, date/time, suspect/victim demographics
- **Also:** CompStat 2.0 (compstat.nypdonline.org) for interactive crime stats at precinct level
- **Why it's interesting:**
  - Seven major crime categories tracked at citywide, borough, and precinct levels
  - Can cross-reference with 311 data, property data, demographic data
  - Temporal and spatial patterns

### D. Restaurant Inspection Results (DOHMH)
- **Source:** https://data.cityofnewyork.us/Health/DOHMH-New-York-City-Restaurant-Inspection-Results/43nn-pn8j
- **Size:** ~30,000 active establishments, with rolling inspection history
- **Fields:** Restaurant name, cuisine type, borough, inspection date, violation code, violation description (text!), grade (A/B/C), score
- **Why it's interesting:**
  - Top violations: unclean surfaces (13.76%), pest harborage (10.38%), evidence of mice (6.81%)
  - 60% of establishments get "A" grades
  - American cuisine dominates (17.48%), then Chinese (8.93%), then coffee/tea (5.57%)
  - Manhattan = 39.2% of inspections; Brooklyn = 25.6%; Queens = 23%
  - **Violation descriptions are free text** -- perfect for LLM classification/summarization

### E. PLUTO (Primary Land Use Tax Lot Output)
- **Source:** https://www.nyc.gov/site/planning/data-maps/open-data/dwn-pluto-mappluto.page
- **Size:** 870,000+ tax lots with 80+ attribute columns
- **Fields:** Zoning designation, land use, building class, number of units, number of floors, lot area, assessed value, year built, owner name, address
- **Why it's interesting:**
  - The most comprehensive property-level dataset for any US city
  - Geospatial (MapPLUTO has GIS shapefiles)
  - Available from 1999 -- can track zoning changes over time
  - Can combine with virtually any other NYC dataset via BBL (Borough-Block-Lot) as join key
  - Includes assessed values -- proxy for gentrification/development

### F. DOB (Dept of Buildings) Complaints & Permits
- **Source:** https://data.cityofnewyork.us/Housing-Development/DOB-Complaints-Received/eabe-havv
- **Size:** 85,000+ complaints filed annually
- **Fields:** Complaint category, disposition, inspection date, BIN (Building ID Number), BBL
- **Why it's interesting:**
  - 67% of complaints result in violations upon inspection
  - HPD issued 895,457 housing code violations in FY2024 (up 24% from prior year)
  - BBL/BIN keys link directly to PLUTO property data
  - Building permits data shows new construction and renovation activity

### G. MTA Subway Ridership
- **Source:** https://web.mta.info/developers/turnstile.html (hourly ridership dataset)
- **Also:** Origin-Destination ridership dataset (new!) -- estimated rider flows between station pairs by hour/day
- **Size:** ~200,000 rows per weekly CSV
- **Fields:** Station, date, time, entries, exits (hourly estimates), fare payment class
- **Why it's interesting:**
  - COVID recovery patterns: poorer neighborhoods saw smaller ridership decreases
  - Origin-Destination data is relatively new and underexplored
  - OMNY tap data improving granularity
  - Combine with 311, crime, property data for neighborhood-level stories

### H. Evictions
- **Source:** https://data.cityofnewyork.us/City-Government/Evictions/6z8x-wfk4
- Updated daily by HPD
- Also: NYU Furman Center research data, Housing Data Coalition, Eviction Lab tracking
- Housing court filing trends from 2019-present via State OCA dashboard

---

## 2. Large Public Datasets That LLMs Could Unlock

These are datasets that are traditionally hard to use because they're unstructured, massive, or require domain expertise -- but LLMs change the equation.

### A. Regulations.gov Public Comments
- **Source:** https://www.regulations.gov/bulkdownload + API (https://open.gsa.gov/api/regulationsgov/)
- **What:** Every public comment submitted on proposed federal regulations (FCC, FDA, EPA, etc.)
- **Size:** Millions of comments across thousands of rulemaking dockets
- **API:** 1,000 requests/hour with free API key; Python wrapper available (github.com/willjobs/regulations-public-comments)
- **LLM unlock:** Classify sentiment, extract key arguments, identify form letters vs. unique comments, summarize thousands of comments into thematic clusters. ICF has already piloted Gen AI for this with federal agencies.
- **Demo potential:** "Ask any federal regulation what the public thinks about it"

### B. SEC EDGAR Filings
- **Source:** https://www.sec.gov/edgar/searchedgar/companysearch (bulk XBRL + TXT)
- **What:** 10-K annual reports, 10-Q quarterly reports, 8-K current reports, proxy statements for all US public companies
- **Size:** Decades of filings, each 10-K can be 100+ pages of dense text
- **LLM unlock:** Extract risk factors, compare year-over-year language changes, identify early warning signals in management discussion sections. Cybersyn has converted unstructured XBRL into relational format. LangChain + Kay have demo'd RAG over SEC filings.
- **Demo potential:** "Chat with any public company's financials" or "What changed in Company X's risk factors this year?"

### C. Congressional Record / Floor Debates
- **Source:** Stanford Congressional Record Dataset (data.stanford.edu/congress_text) -- 43rd through 114th Congress
- **Also:** Cornell ConVote dataset (speeches with support/oppose labels); Congress.gov official records
- **What:** Full text of every speech on the House and Senate floor from 1873-present
- **Size:** 138+ years of speeches
- **LLM unlock:** Track rhetorical shifts over time, identify when evidence-based language declined (researchers found a shift from evidence to intuition since the 1970s), analyze partisan language divergence, link speeches to voting records
- **Demo potential:** "Search congressional history by concept, not keyword" or "How has Congress talked about [topic X] over 150 years?"

### D. Court Opinions & Legal Documents
- **Source:** Pile of Law (Hugging Face: pile-of-law/pile-of-law) -- 256GB of open-source legal text
- **Also:** CourtListener (free.law) for federal court opinions; CUAD for contract understanding; LexGLUE benchmark
- **What:** Court opinions, contracts, administrative rules, legislative records
- **LLM unlock:** Summarize opinions, extract holdings, find relevant precedent, classify contract clauses, make legal research accessible to non-lawyers
- **Demo potential:** "Plain-English legal research" or "Explain this court ruling to me"

### E. Scientific Papers (arXiv / PubMed)
- **Source:** arXiv bulk access; PubMed (16M+ abstracts up to 2025); Semantic Scholar API
- **What:** Full-text research papers across all scientific disciplines
- **LLM unlock:** Semantic search that understands concepts (not just keywords), cross-paper synthesis, automated literature reviews, extract methodology details. Recent work: SemRank framework for scientific paper retrieval; PaperSearchQA for search+reasoning over papers.
- **Demo potential:** "Research assistant that can synthesize across papers" or "Find papers by describing the method you need"

### F. FOIA Logs & Released Documents
- **Source:** MuckRock FOIA Log Explorer (muckrock.com/foi/logs/); FOIA.gov dataset downloads
- **What:** Metadata about every FOIA request made to federal agencies, plus released documents
- **LLM unlock:** Categorize what the public asks for, identify patterns in government transparency (or lack thereof), make released documents searchable
- **Demo potential:** "What does America want to know about its government?"

---

## 3. Unexplored Dataset Combinations

Research from PNAS (2024) confirms that **unusual combinations of datasets are significantly correlated with higher scientific impact**, yet novel pairings remain rare. Here are combinations that seem particularly unexplored:

### High-Potential Mashups

1. **311 Complaints + PLUTO Property Data + Evictions**
   - Join on BBL/address. Track: Do building complaints predict evictions? Do property value changes (via assessed values) correlate with complaint patterns? Can you identify buildings in crisis before residents are displaced?
   - LLM role: Classify free-text 311 descriptions into severity levels, extract building-specific narratives

2. **Restaurant Inspections + 311 Complaints + Yelp Reviews**
   - Cross-reference health violations with public complaints and review sentiment
   - LLM role: Match free-text violation descriptions with Yelp review mentions of cleanliness/pests; predict inspection outcomes from review language

3. **MTA Origin-Destination + Taxi/Rideshare + 311 + Crime**
   - Understand neighborhood accessibility: areas with declining transit but rising rideshare could indicate transit deserts
   - Temporal overlay: Do crime complaints spike when subway service is disrupted?

4. **DOB Building Permits + PLUTO Zoning + 311 Noise/Construction Complaints**
   - Track construction activity's impact on neighborhood livability
   - LLM role: Extract project descriptions from permit text, classify construction type, predict complaint volume

5. **Congressional Record + Regulations.gov Comments + Federal Register**
   - Full lifecycle of policy: what Congress said --> what regulation was proposed --> what the public said about it
   - LLM role: Trace arguments from floor speeches through public comments; identify which arguments "won"

6. **SEC 10-K Risk Factors + News + Stock Performance**
   - Did companies that flagged supply chain risks early outperform? Were new risk factor additions predictive?
   - LLM role: Extract and categorize risk factors, detect year-over-year language changes

7. **NYC City Council Meeting Transcripts + 311 Complaints + Legislation**
   - What issues are residents complaining about? Are council members addressing them? Does legislation follow complaint patterns?
   - LLM role: Topic extraction from meeting transcripts, matching complaint themes to legislative activity

---

## 4. Recent Projects & Demos Using LLMs + Large Datasets

### citymeetings.nyc (Standout Example)
- **What:** AI-powered platform that makes NYC City Council meetings navigable
- **How:** Uses Deepgram AI for transcription + diarization; breaks 7-hour meetings into short, skimmable chapters with summaries; enables "Meeting Miner" search across all meetings
- **Impact:** One person can publish accurate, navigable meeting records same-day
- **URL:** https://citymeetings.nyc/
- **Why it matters:** Turns completely inaccessible unstructured government video into searchable, citable text. Featured on NY1.

### AskNYC.ai
- **What:** AI-powered NYC recommendations with NLP understanding and interactive maps
- **URL:** https://www.asknyc.ai/

### NYC MyCity Chatbot (chat.nyc.gov)
- **What:** Official NYC government chatbot for small business advice (permits, licensing)
- **Notable:** Government-deployed LLM application, piloted by City of New York
- **URL:** https://chat.nyc.gov/

### NYC Open Data + ChatGPT
- **What:** Users uploading datasets directly into ChatGPT for instant analysis
- **Also:** Custom GPTs like "NYC Open Dataset Helper" and "NYC 311 Open Data Tutor"
- **Reference:** Nathan Storey's Medium post on exploring NYC Open Data with ChatGPT

### THE CITY's AI Mapping Project
- **What:** Using ChatGPT to extract locations from journalism stories and map them across NYC
- **How:** LLM reads articles, extracts neighborhoods/addresses/coordinates, generates interactive maps

### Vanna (Open Source, 14k+ GitHub stars)
- **What:** Chat with any SQL database using natural language; text-to-SQL via LLMs with agentic retrieval
- **Supports:** Any LLM (OpenAI, Anthropic, Ollama, etc.) + any database (Postgres, SQLite, DuckDB, etc.)
- **URL:** https://github.com/vanna-ai/vanna
- **Demo potential:** Point at NYC Open Data loaded into a database, chat with it

### ICF / Regulations.gov Gen AI
- **What:** Federal agency pilot using LLMs to process and classify public comments on proposed regulations
- **How:** Secure cloud infrastructure with FedRAMP-compliant LLM services; pressure-tested for accuracy

### SEC Filings RAG Pipeline
- **What:** LangChain + Kay + Cybersyn demo'd RAG over embedded SEC filings
- **How:** Convert unstructured filings to structured data, embed for semantic search, query via natural language

---

## 5. Best Candidates for a Week-Long Ambitious Project

### Tier 1: High Impact, Feasible in a Week, Compelling Demo

**A. "Chat with NYC" -- Natural Language Interface to Multiple NYC Datasets**
- Load 311 + Restaurant Inspections + PLUTO + Crime data into a database
- Build text-to-SQL or RAG interface using Vanna or custom pipeline
- Users ask: "Which neighborhoods have the most noise complaints near new construction?" or "Show me restaurants in Brooklyn with repeat mouse violations"
- **Why compelling:** Democratizes access to 2,700 datasets; visual + conversational
- **Tech:** Vanna/LangChain + DuckDB/Postgres + Streamlit or web frontend

**B. "NYC Building Stories" -- LLM-Powered Building Intelligence**
- For any address: pull together 311 complaints, DOB violations, PLUTO property data, evictions, restaurant inspections (if applicable)
- LLM synthesizes a narrative: "This building at 123 Main St was built in 1920, rezoned in 2015, has had 47 noise complaints, 3 DOB violations for illegal construction, and 2 eviction proceedings in the last year"
- **Why compelling:** Nobody has stitched these together into human-readable building profiles
- **Tech:** Multiple API calls joined on BBL/BIN/address + LLM summarization

**C. "What Does America Think?" -- Public Comment Analyzer for Federal Regulations**
- Pick a hot-button federal regulation (e.g., recent AI regulation, net neutrality, environmental rule)
- Bulk download all public comments via regulations.gov API
- LLM classifies: support/oppose, key arguments, form letters vs. unique, sentiment, geographic patterns
- Visualize the "landscape" of public opinion on a regulation
- **Why compelling:** Makes democratic participation legible; nobody has done this well at scale
- **Tech:** Regulations.gov API + Claude/GPT for classification + visualization

**D. "NYC Council Meeting Intelligence" (extending citymeetings.nyc concept)**
- Download NYC City Council transcripts + match with 311 complaint data + legislation tracker
- "What issues are constituents complaining about that the council hasn't addressed?"
- **Why compelling:** Accountability tool; connects resident voice to government action
- **Tech:** Transcripts + 311 API + LLM topic matching

### Tier 2: Ambitious, Might Need Scoping Down

**E. "SEC Filing Time Machine"**
- Track how a company's risk factors, MD&A language, and forward-looking statements change over years
- LLM extracts and compares year-over-year changes, flags new risks, tracks which predictions came true
- **Challenge:** Parsing SEC filings is notoriously messy

**F. "Policy Lifecycle Tracker"**
- Congressional speech --> proposed regulation --> public comments --> final rule
- Trace an idea from its first mention in Congress through to implementation
- **Challenge:** Requires linking across 3+ datasets with fuzzy matching

**G. "150 Years of Congressional Rhetoric"**
- Semantic search across the full Congressional Record
- "When did Congress first talk about AI?" "How has immigration rhetoric changed?"
- **Challenge:** Stanford dataset requires significant processing

---

## Key Technical Notes

- **BBL (Borough-Block-Lot)** is the universal join key for NYC property datasets
- **BIN (Building Identification Number)** links DOB data
- Most NYC Open Data supports **Socrata API** (SODA) for programmatic access
- TLC data is in **Parquet** format (efficient for large-scale analysis)
- **DuckDB** is excellent for querying large NYC datasets locally without a server
- **Regulations.gov API** requires a free API key (1,000 requests/hour)
- The **Housing Data Coalition** (housingdatanyc.org) has pre-combined NYC housing datasets

## Sources

- [NYC Open Data Portal](https://opendata.cityofnewyork.us/)
- [NYC 311 Service Requests](https://data.cityofnewyork.us/Social-Services/311-Service-Requests-from-2020-to-Present/erm2-nwe9)
- [TLC Trip Record Data](https://www.nyc.gov/site/tlc/about/tlc-trip-record-data.page)
- [Todd Schneider's 1.1B Trip Analysis](https://toddwschneider.com/posts/analyzing-1-1-billion-nyc-taxi-and-uber-trips-with-a-vengeance/)
- [NYPD Complaint Data Historic](https://data.cityofnewyork.us/Public-Safety/NYPD-Complaint-Data-Historic/qgea-i56i)
- [DOHMH Restaurant Inspection Results](https://data.cityofnewyork.us/Health/DOHMH-New-York-City-Restaurant-Inspection-Results/43nn-pn8j)
- [PLUTO Dataset](https://www.nyc.gov/site/planning/data-maps/open-data/dwn-pluto-mappluto.page)
- [DOB Complaints Received](https://data.cityofnewyork.us/Housing-Development/DOB-Complaints-Received/eabe-havv)
- [MTA Subway Hourly Ridership](https://web.mta.info/developers/turnstile.html)
- [Evictions Dataset](https://data.cityofnewyork.us/City-Government/Evictions/6z8x-wfk4)
- [Regulations.gov API](https://open.gsa.gov/api/regulationsgov/)
- [Regulations.gov Bulk Download](https://www.regulations.gov/bulkdownload)
- [Regulations.gov Python Wrapper](https://github.com/willjobs/regulations-public-comments)
- [Pile of Law Dataset](https://huggingface.co/datasets/pile-of-law/pile-of-law)
- [Stanford Congressional Record](https://data.stanford.edu/congress_text)
- [Cornell ConVote Dataset](https://www.cs.cornell.edu/home/llee/data/convote.html)
- [Congressional Rhetoric Shift Study](https://www.nature.com/articles/s41562-025-02136-2)
- [SEC EDGAR](https://www.sec.gov/edgar/searchedgar/companysearch)
- [LangChain + Kay + Cybersyn SEC RAG](https://blog.langchain.com/kay-x-cybersyn-x-langchain/)
- [citymeetings.nyc](https://citymeetings.nyc/)
- [How citymeetings.nyc Works](https://vikramoberoi.com/posts/how-citymeetings-nyc-uses-ai-to-make-it-easy-to-navigate-city-council-meetings/)
- [AskNYC.ai](https://www.asknyc.ai/)
- [NYC MyCity Chatbot](https://chat.nyc.gov/)
- [Vanna - Chat with SQL](https://github.com/vanna-ai/vanna)
- [NYC Open Data with ChatGPT](https://medium.com/@npstorey/using-chatgpt-to-explore-nyc-open-data-0affcb1b8bb2)
- [Dataset Combination & Scientific Impact (PNAS)](https://www.pnas.org/doi/10.1073/pnas.2402802121)
- [ICF Gen AI for Public Comments](https://www.icf.com/clients/technology/regulations-gov-gen-ai-public-comment-analysis)
- [NYC Comptroller 311 Audit](https://comptroller.nyc.gov/newsroom/press-releases/nyc-comptroller-landers-audit-exposes-racial-gaps-in-building-code-enforcement-based-on-311-complaints/)
- [MotherDuck NYC 311 Data](https://motherduck.com/docs/getting-started/sample-data-queries/nyc-311-data/)
- [Housing Data Coalition](https://www.housingdatanyc.org/)
- [Awesome Public Datasets](https://github.com/awesomedata/awesome-public-datasets)
- [Awesome Legal Data](https://github.com/openlegaldata/awesome-legal-data)
- [NYC TLC Factbook Dashboard](https://medium.com/@NYCTLC/introducing-the-tlc-factbook-nyc-tlcs-new-data-dashboard-4eec7f9c5e4c)
- [MTA Origin-Destination Dataset](https://www.mta.info/article/introducing-subway-origin-destination-ridership-dataset)
- [NYU Furman Center Eviction Filings](https://furmancenter.org/research/publication/trends-in-new-york-city-housing-court-eviction-filings)
