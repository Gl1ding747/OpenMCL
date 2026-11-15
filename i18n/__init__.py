import os
import json

LANGS = {"en": "English", "zh": "简体中文"}


class Translator:
    def __init__(self):
        self._current = "en"
        self._strings = {}

    def load(self, lang):
        if lang not in LANGS:
            lang = "en"
        self._current = lang
        self._strings.clear()
        lang_dir = os.path.dirname(os.path.abspath(__file__))
        fp = os.path.join(lang_dir, lang + ".py")
        if not os.path.isfile(fp):
            # 打包 / 资源迁移时的回退：资源根下的 i18n
            from core.paths import resource_path
            fp = os.path.join(resource_path("i18n"), lang + ".py")
        if os.path.isfile(fp):
            d = {}
            with open(fp, "r", encoding="utf-8") as f:
                exec(f.read(), {"__builtins__": {}}, d)
            self._strings = d.get("STRINGS", {})
        return self._current

    def current(self):
        return self._current

    def tr(self, text, *args):
        if self._current == "en":
            result = text
        else:
            result = self._strings.get(text, text)
        if args:
            try:
                result = result.format(*args)
            except Exception:
                pass
        return result


_translator = Translator()


def init_lang(lang="en"):
    return _translator.load(lang)


def tr(text, *args):
    return _translator.tr(text, *args)


def current_lang():
    return _translator.current()


def available_langs():
    return dict(LANGS)