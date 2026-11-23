"""Render the release guide as one offline HTML file using the folder guide's layout."""

from argparse import ArgumentParser
from base64 import b64encode
from html import escape
from pathlib import Path
import re

from markdown_it import MarkdownIt


def inline_screenshot(match, directory):
    description, filename = match.groups()
    path = (directory / filename).resolve(strict=True)
    if not path.is_relative_to((directory / '图片').resolve(strict=True)) or path.suffix != '.png':
        raise ValueError(f'Unexpected guide screenshot: {filename}')
    label = escape(description)
    encoded = b64encode(path.read_bytes()).decode('ascii')
    return (f'<figure class="screenshot"><button class="image-button" type="button" aria-label="放大：{label}">'
            f'<img src="data:image/png;base64,{encoded}" alt="{label}"/></button>'
            f'<figcaption>{label}<span class="zoom-hint"> · 点击图片放大</span></figcaption></figure>')


def render(template, source, target):
    html = template.read_text(encoding='utf-8')
    markdown = source.read_bytes()
    intro, content = source.read_text(encoding='utf-8').split('<!-- guide-body -->', 1)
    engine = MarkdownIt('commonmark', {'html': True}).enable('table')
    content, pictures = re.subn(r'^!\[([^\n]+)\]\((图片/[^\n)]+)\)$',
                                lambda match: inline_screenshot(match, source.parent), content, flags=re.M)
    if pictures != 9:
        raise ValueError(f'Unexpected number of screenshots: {pictures!r}')

    def prose(text):
        return (engine.render(text).replace('<table>', '<div class="table-scroll"><table>')
                .replace('</table>', '</table></div>'))

    def replace_section(before, after, value):
        nonlocal html
        start = html.index(before) + len(before)
        end = html.index(after, start)
        html = html[:start] + value + html[end:]

    replace_section('<article class="intro prose">', '</article>', prose(intro))
    replace_section('<main class="paper prose">', '<footer>', prose(content))
    encoded = b64encode(markdown).decode('ascii')
    html, downloads = re.subn(r'(data:text/markdown;charset=utf-8;base64,)[\w+/=]+',
                              lambda match: match[1] + encoded, html)
    if downloads != 1:
        raise ValueError('Offline guide must include exactly one Markdown download')
    target.write_text(html, encoding='utf-8')


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('template', type=Path)
    parser.add_argument('source', type=Path)
    parser.add_argument('target', type=Path)
    args = parser.parse_args()
    render(args.template, args.source, args.target)


if __name__ == '__main__':
    main()
