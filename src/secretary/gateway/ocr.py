"""OCR service — extract text from images using vision models.

Uses the OpenAI-compatible API with vision-capable models.
Supports fallback chain: try multiple models in order.
"""

from __future__ import annotations

import base64
import logging
import os
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

# Default OCR models (in fallback order)
# These are known to support vision/image input
DEFAULT_OCR_MODELS = [
    "deepseek-v4-flash",  # Fast, good vision support
    "deepseek-v4-pro",  # More capable fallback
]

# API configuration
DEFAULT_API_BASE = "http://127.0.0.1:3300/v1"
DEFAULT_TIMEOUT = 30


def _get_api_config() -> tuple[str, str]:
    """Get API base URL and key from environment."""
    api_base = os.environ.get("NEWAPI_API_BASE", DEFAULT_API_BASE)
    api_key = os.environ.get("NEWAPI_API_KEY", "")
    return api_base, api_key


def _image_to_base64_url(image_path_or_url: str) -> str:
    """Convert image to base64 data URL for vision API.

    Args:
        image_path_or_url: Local file path or HTTP URL

    Returns:
        Data URL string (data:image/jpeg;base64,...)
    """
    if image_path_or_url.startswith(("http://", "https://")):
        # Download image and convert to base64
        try:
            resp = requests.get(image_path_or_url, timeout=10)
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "image/jpeg")
            if ";" in content_type:
                content_type = content_type.split(";")[0].strip()
            b64 = base64.b64encode(resp.content).decode()
            return f"data:{content_type};base64,{b64}"
        except Exception as e:
            logger.warning("Failed to download image %s: %s", image_path_or_url[:80], e)
            raise
    else:
        # Local file
        path = Path(image_path_or_url)
        if not path.exists():
            raise FileNotFoundError(f"Image not found: {image_path_or_url}")
        suffix = path.suffix.lower()
        mime_map = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".gif": "image/gif",
            ".webp": "image/webp",
        }
        mime = mime_map.get(suffix, "image/jpeg")
        b64 = base64.b64encode(path.read_bytes()).decode()
        return f"data:{mime};base64,{b64}"


def extract_text_from_image(
    image_url: str,
    models: list[str] | None = None,
    api_base: str | None = None,
    api_key: str | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> str | None:
    """Extract text from an image using vision models.

    Tries each model in order until one succeeds.

    Args:
        image_url: URL or local path to the image
        models: List of model IDs to try (defaults to DEFAULT_OCR_MODELS)
        api_base: API base URL (defaults to env NEWAPI_API_BASE)
        api_key: API key (defaults to env NEWAPI_API_KEY)
        timeout: Request timeout in seconds

    Returns:
        Extracted text, or None if all models fail
    """
    if models is None:
        models = DEFAULT_OCR_MODELS

    if api_base is None or api_key is None:
        env_base, env_key = _get_api_config()
        if api_base is None:
            api_base = env_base
        if api_key is None:
            api_key = env_key

    # Convert image to base64 data URL
    try:
        data_url = _image_to_base64_url(image_url)
    except Exception as e:
        logger.error("Failed to prepare image for OCR: %s", e)
        return None

    # OCR prompt
    ocr_prompt = (
        "请仔细阅读这张图片中的所有文字内容，包括数字、日期、股票代码、价格等。"
        "只输出图片中的原始文字，不要添加任何解释或分析。"
        "如果有表格或结构化数据，保持原始格式。"
    )

    # Try each model in order
    for model in models:
        try:
            result = _call_vision_api(
                api_base=api_base,
                api_key=api_key,
                model=model,
                image_url=data_url,
                prompt=ocr_prompt,
                timeout=timeout,
            )
            if result:
                logger.info("OCR successful with model %s (%d chars)", model, len(result))
                return result
        except Exception as e:
            logger.warning("OCR failed with model %s: %s", model, str(e)[:100])
            continue

    logger.error("All OCR models failed for image: %s", image_url[:80])
    return None


def _call_vision_api(
    api_base: str,
    api_key: str,
    model: str,
    image_url: str,
    prompt: str,
    timeout: int,
) -> str | None:
    """Call a vision model API to extract text from image.

    Args:
        api_base: API base URL
        api_key: API key
        model: Model ID
        image_url: Base64 data URL of the image
        prompt: OCR prompt
        timeout: Request timeout

    Returns:
        Extracted text or None
    """
    url = f"{api_base}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": image_url},
                    },
                ],
            }
        ],
        "max_tokens": 2000,
        "temperature": 0.1,  # Low temperature for accurate OCR
    }

    resp = requests.post(url, json=payload, headers=headers, timeout=timeout)
    resp.raise_for_status()

    data = resp.json()
    choices = data.get("choices", [])
    if choices:
        content = choices[0].get("message", {}).get("content", "")
        if content and len(content.strip()) > 5:  # Minimum viable OCR result
            return content.strip()

    return None


def extract_text_from_images(
    image_urls: list[str],
    models: list[str] | None = None,
) -> str | None:
    """Extract text from multiple images, combining results.

    Args:
        image_urls: List of image URLs
        models: OCR models to try

    Returns:
        Combined extracted text, or None if all fail
    """
    if not image_urls:
        return None

    all_texts = []
    for url in image_urls:
        text = extract_text_from_image(url, models=models)
        if text:
            all_texts.append(text)

    if all_texts:
        return "\n\n".join(all_texts)
    return None
