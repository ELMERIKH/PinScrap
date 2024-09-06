from flask import Flask, jsonify, request, send_file
import json
import asyncio
import aiohttp
import aiofiles
from bs4 import BeautifulSoup as soup
from pydotmap import DotMap
from flask_cors import CORS
import os
import zipfile

app = Flask(__name__)
CORS(app)  # This will allow all origins

class PinterestImageScraper:
    def __init__(self):
        self.session = None

   
    async def get_pinterest_links(self, body, max_images: int):
        html = soup(body, 'html.parser')
        links = html.select('#b_results cite')
        searched_urls = []
        all_urls = []
        
        for link in links:
            link = link.text
            all_urls.append(link)
            if "pinterest" in link:
                searched_urls.append(link)
                if max_images is not None and len(searched_urls) == max_images:
                    break
        
        return searched_urls, all_urls

    async def get_source(self, url: str):
        try:
            print(f"Fetching source for URL: {url}")  # Debugging line
            async with self.session.get(url) as response:
                text = await response.text()
                html = soup(text, 'html.parser')
                json_data = html.find_all("script", attrs={"id": "__PWS_INITIAL_PROPS__"})
                if not json_data:
                    json_data = html.find_all("script", attrs={"id": "__PWS_DATA__"})
                
                return json.loads(json_data[0].string) if json_data else {}
        except Exception as e:
            print(f"Error fetching source for {url}: {e}")
            return {}

    def save_image_url(self, json_data, max_images: int):
        url_list = []
        try:
            data = DotMap(json_data)
            if not data.initialReduxState and not data.props:
                return []
            pins = data.initialReduxState.pins if data.initialReduxState else data.props.initialReduxState.pins
            for pin in pins:
                if isinstance(pins[pin].images.get("orig"), list):
                    for i in pins[pin].images.get("orig"):
                        url_list.append(i.get("url"))
                        if max_images is not None and len(url_list) == max_images:
                            return list(set(url_list))
                else:
                    url_list.append(pins[pin].images.get("orig").get("url"))
                    if max_images is not None and len(url_list) == max_images:
                        return list(set(url_list))
        except Exception:
            pass
        return list(set(url_list))

    async def start_scraping(self, max_images, key, page=1):
        keyword = f"{key} pinterest".replace(" ", "%20")
        start = (page - 1) * 10 + 1  # Calculate the start index based on the page number
        url = f'https://www.bing.com/search?q={keyword}&first={start}&FORM=PERE'
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36 Edg/125.0.0.0"}
        
        async with self.session.get(url, headers=headers) as response:
            content = await response.text()
            searched_urls, links = await self.get_pinterest_links(content, max_images)
            return searched_urls, key.replace(" ", "_"), response.status, links


    
    async def scrape(self, key: str, max_images: int = None, page: int = 1):
        async with aiohttp.ClientSession() as session:
            self.session = session
            
            # Fetch Pinterest images
            extracted_urls, keyword, search_engine_status_code, links = await self.start_scraping(max_images, key, page)
            
            tasks = [self.get_source(url) for url in extracted_urls]
            json_data_list = await asyncio.gather(*tasks, return_exceptions=True)
            
            urls_list = []
            for json_data in json_data_list:
                if isinstance(json_data, Exception):
                    print(f"Error in task: {json_data}")
                    continue
                urls_list.extend(self.save_image_url(json_data, max_images - len(urls_list)))
                if len(urls_list) >= max_images:
                    break
        
            return {
                "search_engine_status_code": search_engine_status_code,
                "urls_list":  urls_list [:max_images],
                "searched_urls": links,
                "extracted_urls": extracted_urls,
                "keyword": key,
        
            }

scraper = PinterestImageScraper()

@app.route('/scrape/<keyword>', methods=['GET'])
async def scrape_images(keyword):
    max_images = request.args.get('max_images', default=250, type=int)
    page = request.args.get('page', default=1, type=int)  # Get the page number from the request
    details = await scraper.scrape(keyword, max_images=max_images, page=page)

    return jsonify({
        "keyword": details["keyword"],
        "total_urls_found": len(details["urls_list"]),
        "image_urls": details["urls_list"],

    })
@app.route('/download/<keyword>', methods=['GET'])
async def download_images(keyword):
    max_images = request.args.get('max_images', default=250, type=int)
    page = request.args.get('page', default=1, type=int)  # Get the page number from the request
    details = await scraper.scrape(keyword, max_images=max_images, page=page)
    image_urls = details["urls_list"]

    # Create a temporary directory to download images
    temp_dir = "temp_pinterest_images"
    os.makedirs(temp_dir, exist_ok=True)

    async def download_image(url, session, file_path):
        async with session.get(url) as response:
            if response.status == 200:
                async with aiofiles.open(file_path, mode='wb') as f:
                    await f.write(await response.read())

    async with aiohttp.ClientSession() as session:
        tasks = [
            download_image(url, session, os.path.join(temp_dir, f"{i}.jpg"))
            for i, url in enumerate(image_urls)
        ]
        await asyncio.gather(*tasks)

    # Create a zip file from the downloaded images
    zip_path = os.path.join(temp_dir, f"{keyword}.zip")
    with zipfile.ZipFile(zip_path, 'w') as zipf:
        for root, _, files in os.walk(temp_dir):
            for file in files:
                if file.endswith(".jpg"):
                    zipf.write(os.path.join(root, file), arcname=file)

    # Return the zip file as a response
    return send_file(zip_path, as_attachment=True, download_name=f"{keyword}.zip")


if __name__ == '__main__':
    app.run(debug=True)