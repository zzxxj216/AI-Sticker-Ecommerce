import requests
import base64
import mimetypes

url = "https://api.jiekou.ai/v3/gpt-image-2-edit"

def image_to_base64_data_url(image_path):
    mime_type, _ = mimetypes.guess_type(image_path)
    if mime_type is None:
        mime_type = "image/png"

    with open(image_path, "rb") as f:
        base64_data = base64.b64encode(f.read()).decode("utf-8")

    return f"data:{mime_type};base64,{base64_data}"

image_path = r"F:\练习模块\AI-Sticker-Ecommerce\scripts\output\poc_w1\preview.png"
image_base64_url = image_to_base64_data_url(image_path)

payload = {
    "image": image_base64_url,
    "prompt": "There are about 10 stickers in this sticker sheet. Return 10 separate output images, each containing exactly one different sticker from the sheet, in order from left to right and top to bottom.",
    "n": 10,
    "quality": "medium",
    "size": "1024x1024",
    "output_format": "png"
}
headers = {
    "Content-Type": "application/json",
    "Authorization": "Bearer sk_Cu6iTbAGRC-AE6sRrjfDx1V7Wta-NrPzc3d27DJW59E"
}

response = requests.post(url, json=payload, headers=headers, timeout=600)
print(response.status_code)
print(response.text)

