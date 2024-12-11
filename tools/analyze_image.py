import os
import base64
import requests
from openai import OpenAI
from langchain_core.tools import tool
from .config import config

img_config = config.get("img_analysis", {})

client = OpenAI(
    api_key=img_config.get("new_api_key"),
    base_url=img_config.get("new_base_url"),
)

@tool
def analyze_image(query: str, image_input: str):
    """Analyze image content and return AI recognition results.
    Args:
        query: A natural language query for image analysis, e.g., "图中有什么" "详细描述图片".
        image_input: Image source, which can be an image URL (http:// or https://), a Base64 encoded image string, or a Base64 image string with the "image/" prefix.
    """

    img_folder = img_config.get("img_folder")
    os.makedirs(img_folder, exist_ok=True)
    
    if image_input.startswith(('http://', 'https://')):
        try:
            if 'multimedia.nt.qq.com.cn' in image_input and image_input.startswith('https'):
                image_input = image_input.replace('https', 'http', 1)

            file_name = os.path.join(img_folder, "downloaded_image.jpg")
            response = requests.get(image_input, timeout=10)
            response.raise_for_status()
            
            with open(file_name, 'wb') as file:
                file.write(response.content)

            with open(file_name, "rb") as image_file:
                base64_image = base64.b64encode(image_file.read()).decode('utf-8')
            
            image_url = f"data:image/jpeg;base64,{base64_image}"
        except requests.exceptions.RequestException as e:
            print(f"下载失败，错误：{e}")
            raise

    else:
        if image_input.startswith(('data:image/', 'data:application/')):
            image_url = image_input
        else:
            image_url = f"data:image/jpeg;base64,{image_input}"

    completion = client.chat.completions.create(
        model=img_config.get("model"),
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": query,
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": image_url
                        },
                    },
                ],
            }
        ],
    )
    return completion

tools = [analyze_image]