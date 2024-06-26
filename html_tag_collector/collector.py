""" The tag collector is used to collect HTML tags and other relevant data from websites that is useful for training prediction models.
    Information being collected includes:
        - The URL's path
        - HTML title
        - Meta description
        - The root page's HTML title
        - HTTP response code
        - Contents of H1-H6 header tags
        - Contents of div tags
"""


from dataclasses import asdict
from collections import namedtuple
import json
import ssl
import urllib3
import urllib.request
from concurrent.futures import as_completed, ThreadPoolExecutor
import sys
import traceback
import multiprocessing

import requests
from requests_html import AsyncHTMLSession
import asyncio
import pyppeteer
from tqdm import tqdm
from tqdm.asyncio import tqdm
import bs4
from bs4 import BeautifulSoup
import polars as pl
from urllib.parse import urlparse

from RootURLCache import RootURLCache
from common import get_user_agent
from DataClassTags import Tags


# Define the list of header tags we want to extract
header_tags = ["h1", "h2", "h3", "h4", "h5", "h6"]
DEBUG = False  # Set to True to enable debug output
VERBOSE = False  # Set to True to print dataframe each batch
root_url_cache = RootURLCache()


def process_urls(manager_list, render_javascript):
    """Process a list of urls and retrieve their HTML tags.

    Args:
        manager_list (shared list[polars dataframe]): shared list containing the polars dataframe, used to send and retrieve data from outside the current process.
        render_javascript (bool): Whether or not to render webpage's JavaScript rendered HTML.

    Returns:
        list: List of dicts with HTML tags.
    """
    df = manager_list[0]
    urls = df.select(pl.col("url")).rows()
    new_urls = []
    for url in urls:
        if url[0] is not None and not url[0].startswith("http"):
            new_urls.append("https://" + url[0])
        else:
            new_urls.append(url[0])

    loop = asyncio.get_event_loop()
    loop.set_exception_handler(exception_handler)
    future = asyncio.ensure_future(run_get_response(new_urls))
    loop.run_until_complete(future)
    results = future.result()

    results.sort(key=lambda d: d["index"])
    urls_and_responses = [
        {"index": result["index"], "url": urls[i], "response": result["response"]} for i, result in enumerate(results)
    ]

    if render_javascript:
        future = asyncio.ensure_future(render_js(urls_and_responses))
        loop.run_until_complete(future)
        results = future.result()

    urls_and_headers = []
    print("Parsing responses...")
    for url_response in tqdm(urls_and_responses):
        urls_and_headers.append(parse_response(url_response))

    [url_headers.pop("index") for url_headers in urls_and_headers]
    header_tags_df = pl.DataFrame(urls_and_headers)
    clean_header_tags_df = header_tags_df.with_columns(pl.all().fill_null(""))

    # Return updated DataFrame
    manager_list[0] = clean_header_tags_df


def exception_handler(loop, context):
    if DEBUG:
        msg = context.get("exception", context["message"])
        print(msg)


async def run_get_response(urls):
    """Asynchronously retrieves responses from a list of urls.

    Args:
        urls (list): List of urls.

    Returns:
        Future: Future with Response objects.
    """
    tasks = []
    urllib3.disable_warnings()
    session = AsyncHTMLSession(workers=100, browser_args=["--no-sandbox", f"--user-agent={get_user_agent()}"])

    print("Retrieving HTML tags...")
    for i, url in enumerate(urls):
        task = asyncio.ensure_future(get_response(session, url, i))
        tasks.append(task)

    results = await tqdm.gather(*tasks)

    await session.close()
    return results


async def get_response(session, url, index):
    """Retrieves GET response for given url.

    Args:
        session (AsyncHTMLSession): Browser session used to retreive responses.
        url (str): Url to request.
        index (int): Index of the url to keep results in the same order.

    Returns:
        dict(int, Response): Dictionary of the url's index value and Response object, None if an error occurred.
    """
    headers = {
        "User-Agent": get_user_agent(),
        # Make sure there's no pre-mature closing of responses before a redirect completes
        "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9",
    }
    response = None
    if url is not None:
        url = url.removesuffix(".json")

    try:
        response = await session.get(url, headers=headers, timeout=120)
    except (requests.exceptions.SSLError, ssl.SSLError):
        # This error is raised when the website uses a legacy SSL version, which is not supported by requests
        if DEBUG:
            print("SSLError:", url)

        # Retry without SSL verification
        response = await session.get(url, headers=headers, timeout=120, verify=False)
    except requests.exceptions.ConnectionError:
        # Sometimes this error is raised because the provided url uses http when it should be https and the website does not handle it properly
        if DEBUG:
            print("MaxRetryError:", url)

        if not url[4] == "s":
            url = url[:4] + "s" + url[4:]
            # Retry with https
            response = await session.get(url, headers=headers, timeout=120)
    except (urllib3.exceptions.LocationParseError, requests.exceptions.ReadTimeout) as e:
        if DEBUG:
            print(f"{type(e).__name__}: {url}")
    except Exception as e:
        if DEBUG:
            print("Exception:", url)
            print(traceback.format_exc())
            print(str(e))
    finally:
        if DEBUG:
            print(url, response)

        content_type = None
        try:
            content_type = response.headers["content-type"]
        except (KeyError, AttributeError):
            pass

        response = response_valid(response, content_type, url)

        return {"index": index, "response": response}


def response_valid(response, content_type, url):
    """Checks the response to see if content is too large, unreadable, or invalid response code. The response is discarded if it is invalid.

    Args:
        response (Response): Response object to check.
        content_type (str): The content type returned by the website.
        url (str): URL that was requested.

    Returns:
        Response: The response object is returned either unmodified or discarded.
    """    
    # If the response size is greater than 10 MB
    # or the response is an unreadable content type
    # or the response code from the website is not in the 200s
    if (
        response is not None
        and len(response.content) > 10000000
        or content_type is not None
        and any(
            filtered_type in content_type
            for filtered_type in ["pdf", "excel", "msword", "image", "rtf", "zip", "octet", "csv", "json"]
        )
        or response is not None
        and not response.ok
    ):
        # Discard the response content to prevent out of memory errors
        if DEBUG:
            print("Large or unreadable content discarded:", len(response.content), url)
        new_response = requests.Response()
        new_response.status_code = response.status_code
        response = new_response
    
    return response


async def render_js(urls_responses):
    """Renders JavaScript from a list of urls.

    Args:
        urls_responses (dict): Dictionary containing urls and their responses.
    """
    print("Rendering JavaScript...")
    for url_response in tqdm(urls_responses):
        res = url_response["response"]

        if res is None or not res.ok:
            continue

        if DEBUG:
            print("Rendering", url_response["url"][0])
        try:
            task = asyncio.create_task(res.html.arender())
        except AttributeError:
            continue

        # Some websites will cause the rendering to hang indefinitely so we cancel the task if more than 15 seconds have elapsed
        time_elapsed = 0
        while not task.done():
            time_elapsed += 1
            await asyncio.sleep(0.1)

            if time_elapsed > 150:
                task.cancel()
                break

        try:
            await task
        except (pyppeteer.errors.PageError, pyppeteer.errors.NetworkError) as e:
            if DEBUG:
                print(f"{type(e).__name__}")
        except Exception as e:
            if DEBUG:
                print(traceback.format_exc())
                print(str(e))
        except asyncio.CancelledError:
            if DEBUG:
                print("Rendering cancelled")


def parse_response(url_response):
    """Parses relevant HTML tags from a Response object into a dictionary.

    Args:
        url_response (list[dict]): List of dictionaries containing urls and their responses.

    Returns:
        dict: Dictionary containing the url and relevant HTML tags.
    """
    tags = Tags()
    res = url_response["response"]
    tags.index = url_response["index"]

    tags.url, tags.url_path = get_url(url_response)

    tags.root_page_title = remove_excess_whitespace(root_url_cache.get_title(tags.url))

    verified, tags.http_response = verify_response(res)
    if verified is False:
        return asdict(tags)

    parser = get_parser(res)
    if parser is False:
        return asdict(tags)

    try:
        soup = BeautifulSoup(res.html.html, parser)
    except (bs4.builder.ParserRejectedMarkup, AssertionError, AttributeError):
        return asdict(tags)

    tags.html_title = get_html_title(soup)

    tags.meta_description = get_meta_description(soup)

    tags = get_header_tags(tags, soup)

    tags.div_text = get_div_text(soup)

    # Prevents most bs4 memory leaks
    if soup.html:
        soup.html.decompose()

    return asdict(tags)


def get_url(url_response):
    """Returns the url and url_path.

    Args:
        url_response (list[dict]): List of dictionaries containing urls and their responses.

    Returns:
        (str, str): Tuple with the url and url_path.
    """
    url = url_response["url"][0]
    new_url = url
    if not new_url.startswith("http"):
        new_url = "https://" + new_url

    # Drop hostname from urls to reduce training bias
    url_path = urlparse(new_url).path[1:]
    # Remove trailing backslash
    if url_path and url_path[-1] == "/":
        url_path = url_path[:-1]

    return url, url_path


def verify_response(res):
    """Verifies the webpage response is readable and ok.

    Args:
        res (HTMLResponse|Response): Response object to verify.

    Returns:
        VerifiedResponse(bool, int): A named tuple containing False if verification fails, True otherwise and the http response code.
    """
    VerifiedResponse = namedtuple("VerifiedResponse", "verified http_response")
    # The response is None if there was an error during connection, meaning there is no content to read
    if res is None:
        return VerifiedResponse(False, -1)

    # If the connection did not return a 200 code, we can assume there is no relevant content to read
    http_response = res.status_code
    if not res.ok:
        return VerifiedResponse(False, http_response)

    return VerifiedResponse(True, http_response)


def get_parser(res):
    """Retrieves the parser type to use with BeautifulSoup.

    Args:
        res (HTMLResponse|Response): Response object to read the content-type from.

    Returns:
        str|bool: A string of the parser to use, or False if not readable.
    """
    # Attempt to read the content-type, set the parser accordingly to avoid warning messages
    try:
        content_type = res.headers["content-type"]
    except KeyError:
        return False

    # If content type does not contain "html" or "xml" then we can assume that the content is unreadable
    if "html" in content_type:
        parser = "lxml"
    elif "xml" in content_type:
        parser = "lxml-xml"
    else:
        return False

    return parser


def get_html_title(soup):
    """Retrieves the HTML title from a BeautifulSoup object.

    Args:
        soup (BeautifulSoup): BeautifulSoup object to pull the HTML title from.

    Returns:
        str: The HTML title.
    """
    html_title = ""

    if soup.title is not None and soup.title.string is not None:
        html_title = remove_excess_whitespace(soup.title.string)

    return html_title


def get_meta_description(soup):
    """Retrieves the meta description from a BeautifulSoup object.

    Args:
        soup (BeautifulSoup): BeautifulSoup object to pull the meta description from.

    Returns:
        str: The meta description.
    """
    meta_tag = soup.find("meta", attrs={"name": "description"})
    try:
        meta_description = remove_excess_whitespace(meta_tag["content"]) if meta_tag is not None else ""
    except KeyError:
        return ""

    return meta_description


def get_header_tags(tags, soup):
    """Updates the Tags DataClass with the header tags.

    Args:
        tags (Tags): DataClass for relevant HTML tags.
        soup (BeautifulSoup): BeautifulSoup object to pull the header tags from.

    Returns:
        Tags: DataClass with updated header tags.
    """
    for header_tag in header_tags:
        headers = soup.find_all(header_tag)
        # Retrieves and drops headers containing links to reduce training bias
        header_content = [header.get_text(" ", strip=True) for header in headers if not header.a]
        tag_content = json.dumps(header_content, ensure_ascii=False)
        setattr(tags, header_tag, tag_content)

    return tags


def get_div_text(soup):
    """Retrieves the div text from a BeautifulSoup object.

    Args:
        soup (BeautifulSoup): BeautifulSoup object to pull the div text from.

    Returns:
        str: The div text.
    """
    # Extract max 500 words of text from HTML <div>'s
    div_text = ""
    MAX_WORDS = 500
    for div in soup.find_all("div"):
        text = div.get_text(" ", strip=True)
        if text:
            # Check if adding the current text exceeds the word limit
            if len(div_text.split()) + len(text.split()) <= MAX_WORDS:
                div_text += text + " "
            else:
                break  # Stop adding text if word limit is reached

    # Truncate to 5000 characters in case of run-on 'words'
    div_text = div_text[: MAX_WORDS * 10]

    return div_text


def remove_excess_whitespace(s):
    """Removes leading, trailing, and excess adjacent whitespace.

    Args:
        s (str): String to remove whitespace from.

    Returns:
        str: Clean string with excess whitespace stripped.
    """
    return " ".join(s.split()).strip()


def collector_main(df, render_javascript=False):
    context = multiprocessing.get_context("spawn")
    manager = context.Manager()
    manager_list = manager.list([df])

    process = context.Process(target=process_urls, args=(manager_list, render_javascript))
    process.start()
    process.join()

    return manager_list[0]


def process_in_batches(df, render_javascript=False, batch_size=200):
    """Runs the tag collector on small batches of URLs contained in df

    Args:
        df: polars dataframe containing all URLs in a 'url' column
        render_javascript (bool): Whether or not to render webpage's JavaScript rendered HTML.
        batch_size: (int) # of URLs to process at a time (default 200)

    Returns:
        cumulative_df: polars dataframe containing responses from all batches

    """
    # Create an empty DataFrame to store the final cumulative result
    cumulative_df = pl.DataFrame()

    # Calculate the number of batches needed
    num_batches = (len(df) + batch_size - 1) // batch_size

    print(f"\nTotal samples: {len(df)}")
    print(f"Batch size: {batch_size}")
    print(f"Number of Batches: {num_batches} \n")

    # Process the DataFrame in batches
    for i in range(num_batches):
        start_idx = i * batch_size
        end_idx = min((i + 1) * batch_size, len(df))
        batch_df = df[start_idx:end_idx]

        print(f"\nBatch {i + 1}/{num_batches}")

        # Call the collector_main function on the current batch
        header_tags_df = collector_main(batch_df, render_javascript=render_javascript)

        # Check if it's the first batch, then directly set result_df
        # if not then append latest batch on bottom
        if i == 0:
            cumulative_df = header_tags_df
        else:
            cumulative_df = pl.concat([cumulative_df, header_tags_df], how="vertical")

    return cumulative_df


if __name__ == "__main__":
    file = sys.argv[1]

    if file.endswith(".json"):
        with open(file) as f:
            data = json.load(f)
        df = pl.DataFrame(data)
    elif file.endswith(".csv"):
        df = pl.read_csv(file)
    else:
        print("Unsupported filetype")
        sys.exit(1)

    render_javascript = False
    if len(sys.argv) > 2 and sys.argv[2] == "--render-javascript":
        render_javascript = True

    # returns cumulative dataframe (contains all batches) of url and headers tags
    cumulative_df = process_in_batches(df, render_javascript=render_javascript)

    # Drop duplicate columns
    df = df.drop(["http_response", "html_title", "meta_description", "root_page_title", *header_tags])
    # join the url/header tag df with the original df which contains the labels (order has been preserved)
    # remove duplicate rows
    out_df = df.join(cumulative_df, on="url", how="left")
    out_df = out_df.unique(subset=["url"], maintain_order=True)

    if VERBOSE:
        print(out_df)

    # Write the updated JSON data to a new file
    out_df.write_csv("labeled-source-text.csv")
