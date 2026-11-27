"""Use the installed Windows OCR language on rendered pages of the selected file."""
import asyncio

from PIL import Image


def recognize(images, language):
    from winrt.windows.globalization import Language
    from winrt.windows.graphics.imaging import BitmapPixelFormat, SoftwareBitmap
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage.streams import DataWriter

    engine = (OcrEngine.try_create_from_language(Language(language)) if language
              else OcrEngine.try_create_from_user_profile_languages())
    if engine is None:
        langs = ', '.join(lang.language_tag for lang in OcrEngine.available_recognizer_languages)
        raise ValueError('Windows OCR 缺少所选语言，可用语言：' + langs)

    async def run():
        texts = []
        for path in images:
            with Image.open(path) as image:
                gray = image.convert('L')
                with SoftwareBitmap(BitmapPixelFormat.GRAY8, gray.width, gray.height) as bitmap:
                    with DataWriter() as writer:
                        writer.write_bytes(gray.tobytes())
                        bitmap.copy_from_buffer(writer.detach_buffer())
                    result = await engine.recognize_async(bitmap)
                    texts.append('\n'.join(line.text for line in result.lines))
        return texts

    return asyncio.run(run()), engine.recognizer_language.language_tag
