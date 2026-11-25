"""Receipt-backed quick searches within the existing collection-task page."""
from pathlib import Path


def extend(page):
    assets = Path(__file__).with_name('assets')
    section = '<section id="tasksView" hidden>'
    if any(page.count(marker) != 1 for marker in (section, '</head>', '</body>')):
        raise ValueError('Quick task insertion points changed')
    controls = (assets / 'quick-task-list.html').read_text()
    css = (assets / 'quick-task-list.css').read_text()
    js = (assets / 'quick-task-list.js').read_text()
    return (page.replace(section, section + controls, 1)
            .replace('</head>', '<style id="quickTaskStyles">' + css + '</style></head>', 1)
            .replace('</body>', '<script id="quickTaskScript">' + js + '</script></body>', 1))
