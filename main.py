import os
import importlib
import asyncio
from functools import lru_cache
import logging
logging.basicConfig(level=logging.DEBUG)

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware  
from fastapi.templating import Jinja2Templates

from pydantic import BaseModel, HttpUrl
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional

from crawl4ai.web_crawler import WebCrawler
from crawl4ai.database import get_total_count, clear_db

# Configuration
__location__ = os.path.realpath(os.path.join(os.getcwd(), os.path.dirname(__file__)))
MAX_CONCURRENT_REQUESTS = 10  # Adjust this to change the maximum concurrent requests
current_requests = 0
lock = asyncio.Lock()

app = FastAPI()

# CORS configuration
origins = ["*"]  # Allow all origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,  # List of origins that are allowed to make requests
    allow_credentials=True,
    allow_methods=["*"],  # Allows all methods
    allow_headers=["*"],  # Allows all headers
)

# Mount the pages directory as a static directory
app.mount("/pages", StaticFiles(directory=__location__ + "/pages"), name="pages")
templates = Jinja2Templates(directory=__location__ + "/pages")
# chromedriver_autoinstaller.install()  # Ensure chromedriver is installed
@lru_cache()
def get_crawler():
    # Initialize and return a WebCrawler instance
    return WebCrawler(verbose = True)

class CrawlRequest(BaseModel):
    urls: List[str]
    include_raw_html: Optional[bool] = False
    bypass_cache: bool = False
    extract_blocks: bool = True
    word_count_threshold: Optional[int] = 5
    extraction_strategy: Optional[str] = "NoExtractionStrategy"
    extraction_strategy_args: Optional[dict] = {}
    chunking_strategy: Optional[str] = "RegexChunking"
    chunking_strategy_args: Optional[dict] = {}
    css_selector: Optional[str] = None
    verbose: Optional[bool] = True


@app.get("/", response_class=HTMLResponse)
async def read_index(request: Request):
    partials_dir = os.path.join(__location__, "pages", "partial")
    partials = {}

    for filename in os.listdir(partials_dir):
        if filename.endswith(".html"):
            with open(os.path.join(partials_dir, filename), "r") as file:
                partials[filename[:-5]] = file.read()

    return templates.TemplateResponse("index.html", {"request": request, **partials})

@app.get("/total-count")
async def get_total_url_count():
    count = get_total_count()
    return JSONResponse(content={"count": count})

# Add endpoit to clear db
@app.get("/clear-db")
async def clear_database():
    # clear_db()
    return JSONResponse(content={"message": "Database cleared."})

def import_strategy(module_name: str, class_name: str, *args, **kwargs):
    try:
        module = importlib.import_module(module_name)
        strategy_class = getattr(module, class_name)
        return strategy_class(*args, **kwargs)
    except ImportError:
        print("ImportError: Module not found.")
        raise HTTPException(status_code=400, detail=f"Module {module_name} not found.")
    except AttributeError:
        print("AttributeError: Class not found.")
        raise HTTPException(status_code=400, detail=f"Class {class_name} not found in {module_name}.")

@app.post("/crawl")
async def crawl_urls(crawl_request: CrawlRequest, request: Request):
    logging.debug(f"[LOG] Crawl request for URL: {crawl_request.urls}")
    global current_requests
    async with lock:
        if current_requests >= MAX_CONCURRENT_REQUESTS:
            raise HTTPException(status_code=429, detail="Too many requests - please try again later.")
        current_requests += 1

    try:
        logging.debug("[LOG] Loading extraction and chunking strategies...")
        crawl_request.extraction_strategy_args['verbose'] = True
        crawl_request.chunking_strategy_args['verbose'] = True
        
        extraction_strategy = import_strategy("crawl4ai.extraction_strategy", crawl_request.extraction_strategy, **crawl_request.extraction_strategy_args)
        chunking_strategy = import_strategy("crawl4ai.chunking_strategy", crawl_request.chunking_strategy, **crawl_request.chunking_strategy_args)

        # Use ThreadPoolExecutor to run the synchronous WebCrawler in async manner
        logging.debug("[LOG] Running the WebCrawler...")
        with ThreadPoolExecutor() as executor:
            loop = asyncio.get_event_loop()
            futures = [
                loop.run_in_executor(
                    executor, 
                    get_crawler().run,
                    str(url),
                    crawl_request.word_count_threshold,
                    extraction_strategy,
                    chunking_strategy,
                    crawl_request.bypass_cache,
                    crawl_request.css_selector,
                    crawl_request.verbose
                )
                for url in crawl_request.urls
            ]
            results = await asyncio.gather(*futures)

        # if include_raw_html is False, remove the raw HTML content from the results
        if not crawl_request.include_raw_html:
            for result in results:
                result.html = None

        return {"results": [result.dict() for result in results]}
    finally:
        async with lock:
            current_requests -= 1
            
@app.get("/strategies/extraction", response_class=JSONResponse)
async def get_extraction_strategies():
    # Load docs/extraction_strategies.json" and return as JSON response
    with open(f"{__location__}/docs/extraction_strategies.json", "r") as file:
        return JSONResponse(content=file.read())

@app.get("/strategies/chunking", response_class=JSONResponse)
async def get_chunking_strategies():
    with open(f"{__location__}/docs/chunking_strategies.json", "r") as file:
        return JSONResponse(content=file.read())
    
            
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)