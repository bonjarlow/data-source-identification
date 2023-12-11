# HTML tag collector
This script adds HTML properties to a JSON file of existing URLs.
*Properties added:* `title`, `meta`, and `header` HTML tags, `http_response`

# How to use
1. If running from the command line, pass the name of the file you want to run as an argument and make sure your file in the same directory. It should be populated with URLs and properties as in the example provided. If importing collector_main, it expects a polars dataframe as an input.
2. Optionally, create a virtual environment. This is especially useful if you don't already have `beautifulsoup4` and `requests` and `polars` installed. In your terminal:

```commandline
python -m venv collector-environment
source collector-environment/bin/activate
```

3. Now install the required python libraries:

```commandline
$pip install -r requirements.txt
```

4. Run `python3 collector.py`.
5. If running from the command line, check the directory: you should now have a `urls_and_headers.csv` file. Invalid URLs are removed. Otherewise the function returns a processed polars dataframe.

# Why does this exist?
We can use machine learning to predict whether a URL is relevant with some success, but labelers otherwise need to visit a URL in order to determine what is kept there. By adding these properties we can label data without navigating to the URL as often.