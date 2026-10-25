"""Formato de descripciones permitido por ProductDetailNuevo.jsx."""
from html import escape
from html.parser import HTMLParser

from markupsafe import Markup


class RichDescription(HTMLParser):
    allowed = {'strong','em','b','i','u','br','p','div','ul','ol','li'}
    hidden = {'script','style','iframe','object','template'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.output = []
        self.stack = []
        self.suppressed = []

    def handle_starttag(self, tag, attrs):
        if self.suppressed:
            if tag in self.hidden:
                self.suppressed.append(tag)
            return
        if tag in self.hidden:
            self.suppressed.append(tag)
            return
        if tag in self.allowed:
            tag = {'b':'strong','i':'em'}.get(tag,tag)
            self.output.append(f'<{tag}>')
            if tag != 'br':
                self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag != 'br':
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if self.suppressed:
            if tag == self.suppressed[-1]:
                self.suppressed.pop()
            return
        tag = {'b':'strong','i':'em'}.get(tag,tag)
        if tag in self.stack:
            while self.stack:
                current = self.stack.pop()
                self.output.append(f'</{current}>')
                if current == tag:
                    break

    def handle_data(self, data):
        if not self.suppressed:
            self.output.append(escape(data))


def rich_description(value):
    parser = RichDescription()
    parser.feed(value or '')
    parser.close()
    while parser.stack:
        parser.output.append(f'</{parser.stack.pop()}>')
    return Markup(''.join(parser.output))
