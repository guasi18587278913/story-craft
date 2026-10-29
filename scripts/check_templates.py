#!/usr/bin/env python3
"""核故事卡和写法卡：五块、状态行、准入两组、路由目录链接、卡片指纹与本机路径。

默认只查本地；--rebuild 在卡片审过后重建 references/story-cards/catalog.json；
--source-dir 接保存的 get_page 原页，逐行核故事卡里引用的案例原文。"""
import sys
sys.dont_write_bytecode = True
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
DIRS = {"story": Path("references/story-cards"), "writing": Path("references/writing-cards")}
LABELS = {"story": "故事卡", "writing": "写法卡"}
FIELDS = {"story": ["id", "name", "file", "status", "card_sha256"],
          "writing": ["id", "name", "file", "use", "card_sha256"]}
FILE_NAMES = {"story": re.compile(r"^([0-9]+)-(.+)\.md$"),
              "writing": re.compile(r"^([A-Z]{2}[0-9]{2})-(.+)\.md$")}
CATALOG = "catalog.json"
INDEX = "index.md"
BLOCKS = ["1", "2", "3", "4", "5"]
STATUS = "状态："
STORY_PHRASES = ["缺了就不成立", "碰巧有的", "可删、可合并", "按材料调整"]
SKELETON_CARDS = ["27", "29", "31"]
SKELETON_PHRASE = "使用方式：只能照骨架试写"
WHOLE, PARTIAL = "整篇组织", "局部表达"
# 拼出本机用户目录的前缀，免得脚本自己带上这串字符。
LOCAL_PREFIX = "/" + "Users" + "/"
REBUILD_HINT = "改完卡片要审过再运行 check_templates.py --rebuild"
LINK = re.compile(r"!?\[[^\]\n]*\]\(([^)]+)\)")
FENCE = re.compile(r"```.*?```", re.S)
QUOTE_LABEL = re.compile(r"^> (\*\*[^*]+\*\*)?")


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def validate_edits(raw, edits):
    chunks = []
    cursor = 0
    for edit in edits:
        start, end = edit["start"], edit["end"]
        if type(start) is not int or type(end) is not int or not cursor <= start < end <= len(raw):
            raise ValueError("匿名位置重叠或越界")
        if digest(raw[start:end]) != edit["original_sha256"]:
            raise ValueError("匿名位置的原文指纹不一致")
        if not isinstance(edit["replacement"], str) or not edit["replacement"]:
            raise ValueError("匿名替换为空")
        chunks.extend([raw[cursor:start], edit["replacement"]])
        cursor = end
    chunks.append(raw[cursor:])
    return "".join(chunks)


def canonical_id(value):
    """故事卡编号去掉前导零（09 写成 9）；写法卡编号统一大写（lx05 写成 LX05）。"""
    value = str(value).strip()
    return str(int(value)) if re.fullmatch(r"[0-9]+", value) else value.upper()


def is_card_id(value):
    """故事卡编号是数字，写法卡编号是两个大写字母加两位数字。"""
    return re.fullmatch(r"[0-9]+|[A-Z]{2}[0-9]{2}", canonical_id(value)) is not None


def read_text(path):
    """按 UTF-8 读文本；读不了时报出是哪个文件。"""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(path.name + " 读不了（要 UTF-8 文本）：" + str(exc))


def card_label(kind, entry):
    return LABELS[kind] + " " + str(entry.get("id", "?"))


def card_dir(root, kind):
    root = Path(root)
    folder = root / DIRS[kind]
    for path in (root / "references", folder):
        if path.is_symlink():
            raise ValueError("卡片目录不能是软链：" + path.relative_to(root).as_posix())
    if not folder.is_dir():
        raise ValueError("卡片目录不存在：" + DIRS[kind].as_posix())
    return folder


def card_path(root, kind, name):
    folder = card_dir(root, kind)
    if not isinstance(name, str) or not name or name in (".", "..") or Path(name).name != name:
        raise ValueError("卡片必须是库内文件名：" + repr(name))
    path = folder / name
    if path.is_symlink():
        raise ValueError("卡片不能是软链：" + name)
    if not path.is_file():
        raise ValueError("文件不存在：" + name)
    path.resolve().relative_to(folder.resolve())
    return path


def read_index(root=ROOT):
    path = card_dir(root, "story") / INDEX
    if path.is_symlink():
        raise ValueError("路由目录 index.md 不能是软链")
    if not path.is_file():
        raise ValueError("路由目录 index.md 不存在")
    return read_text(path)


def load_catalog(root=ROOT):
    path = card_dir(root, "story") / CATALOG
    if path.is_symlink():
        raise ValueError("catalog.json 不能是软链")
    if not path.is_file():
        raise ValueError("catalog.json 不存在：" + REBUILD_HINT)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ValueError("catalog.json 不是有效的 JSON：" + str(exc))
    if not isinstance(data, dict) or sorted(data) != ["story", "writing"]:
        raise ValueError("catalog.json 只能有 story 和 writing 两组")
    for kind, fields in FIELDS.items():
        if not isinstance(data[kind], list):
            raise ValueError("catalog.json 的 " + kind + " 必须是列表")
        for entry in data[kind]:
            if (not isinstance(entry, dict) or sorted(entry) != sorted(fields)
                    or not all(isinstance(entry[field], str) for field in fields)):
                raise ValueError("catalog.json 的 " + kind + " 条目字段应为：" + "、".join(fields))
            if not re.fullmatch(r"[0-9a-f]{64}", entry["card_sha256"]):
                raise ValueError("catalog.json 里" + card_label(kind, entry) + " 的指纹格式不对")
    return data


def scan(root, kind):
    """列出卡片目录里的卡；认不出卡号的 .md 文件和软链另列为错误。"""
    folder = card_dir(root, kind)
    cards, errors = [], []
    for path in sorted(folder.iterdir()):
        name = path.name
        where = DIRS[kind].as_posix() + "/" + name
        if path.is_symlink():
            errors.append(where + " 是软链，卡片目录里不放软链")
            continue
        if kind == "story" and name in (CATALOG, INDEX):
            continue
        if not path.is_file() or not name.endswith(".md"):
            continue
        match = FILE_NAMES[kind].match(name)
        if not match:
            errors.append(where + " 认不出卡号")
            continue
        cards.append({"id": canonical_id(match.group(1)), "name": match.group(2), "file": name})
    return cards, errors


def status_of(text):
    lines = text.split("\n")
    line = lines[2].rstrip("\r") if len(lines) >= 3 else ""
    return line[len(STATUS):].strip() if line.startswith(STATUS) else None


def block_errors(label, text):
    numbers = re.findall(r"^## ([0-9]+)\.", FENCE.sub("", text), re.M)
    if numbers == BLOCKS:
        return []
    missing = [n for n in BLOCKS if n not in numbers]
    if missing:
        return [label + " 五块不齐：缺 " + "、".join("## " + n + "." for n in missing)]
    return [label + " 五块顺序或数量不对：" + "、".join("## " + n + "." for n in numbers)]


def whole_files(root, index_text):
    """路由目录“整篇组织”那张表链到的写法卡文件名。"""
    lines = FENCE.sub("", index_text).split("\n")
    start = level = None
    for number, line in enumerate(lines):
        match = re.match(r"^(#{1,6})\s+(.*)$", line)
        if match and match.group(2).strip().startswith(WHOLE):
            start, level = number, len(match.group(1))
            break
    if start is None:
        raise ValueError("路由目录里找不到“" + WHOLE + "”那一节")
    index_dir = card_dir(root, "story")
    writing = card_dir(root, "writing").resolve()
    names, in_table = set(), False
    for line in lines[start + 1:]:
        match = re.match(r"^(#{1,6})\s", line)
        if match and len(match.group(1)) <= level:
            break
        if not line.lstrip().startswith("|"):
            if in_table:
                break  # 只认这一节的第一张表
            continue
        in_table = True
        for target in LINK.findall(line):
            part = unquote(target.strip().strip("<>")).partition("#")[0]
            if part and (index_dir / part).parent.resolve() == writing:
                names.add(Path(part).name)
    return names


def build_catalog(root=ROOT):
    """按卡片目录和路由目录算出 catalog；文件名认不出或卡号重复时报错。"""
    root = Path(root)
    found, errors = {}, []
    for kind in ("story", "writing"):
        found[kind], scan_errors = scan(root, kind)
        errors.extend(scan_errors)
        seen = {}
        for card in found[kind]:
            if card["id"] in seen:
                errors.append(LABELS[kind] + "卡号重复：" + seen[card["id"]] + "、" + card["file"])
            seen[card["id"]] = card["file"]
    if errors:
        raise ValueError("；".join(errors))
    whole = whole_files(root, read_index(root))
    data = {"story": [], "writing": []}
    for card in sorted(found["story"], key=lambda c: int(c["id"])):
        text = read_text(card_path(root, "story", card["file"]))
        data["story"].append({"id": card["id"], "name": card["name"], "file": card["file"],
                              "status": status_of(text) or "", "card_sha256": digest(text)})
    for card in sorted(found["writing"], key=lambda c: c["id"]):
        text = read_text(card_path(root, "writing", card["file"]))
        data["writing"].append({"id": card["id"], "name": card["name"], "file": card["file"],
                                "use": WHOLE if card["file"] in whole else PARTIAL,
                                "card_sha256": digest(text)})
    return data


def find_card(data, card_id):
    wanted = canonical_id(card_id)
    for kind in ("story", "writing"):
        for entry in data[kind]:
            if entry["id"] == wanted:
                return kind, entry
    return None, None


def read_card(card_id, root=ROOT, data=None):
    """按编号读整张卡；先核 catalog 里的指纹，对不上就拒绝。"""
    data = load_catalog(root) if data is None else data
    kind, entry = find_card(data, card_id)
    if entry is None:
        raise ValueError("没有这张卡：" + str(card_id) + "；用 --list-templates 看路由目录")
    text = read_text(card_path(root, kind, entry["file"]))
    if digest(text) != entry["card_sha256"]:
        raise ValueError(card_label(kind, entry) + " 的指纹和 catalog 不一致：" + REBUILD_HINT)
    return kind, entry, text


def load_sources(paths):
    """读保存的 get_page 原页（目录或 JSON 文件），返回去掉空白的正文。"""
    from check_integrity import pages
    files = []
    for value in paths:
        path = Path(value)
        if path.is_dir():
            found = sorted(p for p in path.glob("*.json") if p.is_file())
            if not found:
                raise ValueError("目录里没有原页 JSON：" + str(path))
            files.extend(found)
        elif path.is_file():
            files.append(path)
        else:
            raise ValueError("原页位置不存在：" + str(path))
    texts = []
    for path in files:
        try:
            got = [page for _, page in pages(json.loads(path.read_text(encoding="utf-8")))]
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise ValueError("原页读不了：" + path.name + "：" + str(exc))
        if not got:
            raise ValueError("不是 get_page 原页：" + path.name)
        for page in got:
            body = page.get("compiled_truth")
            if not isinstance(body, str) or not body.strip():
                raise ValueError("原页正文为空：" + path.name)
            texts.append(re.sub(r"\s", "", body))
    return {"files": len(files), "texts": texts}


def source_errors(data, texts, sources):
    """故事卡里以 "> " 开头的行去掉行首段落标签和所有空白后，要能在某个原页正文里找到。"""
    errors, count = [], 0
    for entry in data["story"]:
        text = texts.get(("story", entry["id"]))
        if text is None:
            continue
        for number, line in enumerate(text.split("\n"), 1):
            if not line.startswith("> "):
                continue
            body = re.sub(r"\s", "", QUOTE_LABEL.sub("", line, count=1))
            if not body:
                continue
            count += 1
            if not any(body in page for page in sources["texts"]):
                errors.append(card_label("story", entry) + " 第 " + str(number) + " 行在原页里找不到：" + line[:80])
    return errors, count


def validate(root=ROOT, sources=None, catalog=None):
    root = Path(root)
    checks, errors = [], []

    def record(item, checked, found):
        checks.append({"item": item, "checked": checked, "errors": len(found)})
        errors.extend(found)

    def summary(cards, source_check="not_run"):
        return {"cards": cards, "checks": checks, "source_check": source_check,
                "errors": errors, "error_count": len(errors)}

    try:
        data = load_catalog(root) if catalog is None else catalog
    except (OSError, ValueError) as exc:
        record("catalog：能读、格式对", 1, [str(exc)])
        return summary({"story": 0, "writing": 0})
    entries = [(kind, entry) for kind in ("story", "writing") for entry in data[kind]]
    texts = {}

    found, ids, files = [], set(), set()
    for kind, entry in entries:
        label = card_label(kind, entry)
        if entry["id"] in ids or (kind, entry["file"]) in files:
            found.append("catalog 里卡号或文件重复：" + label + "，" + entry["file"])
            continue
        ids.add(entry["id"])
        files.add((kind, entry["file"]))
        match = FILE_NAMES[kind].match(entry["file"])
        if not match or canonical_id(match.group(1)) != entry["id"] or match.group(2) != entry["name"]:
            found.append(label + " 的编号、名字和文件名对不上：" + entry["file"])
        try:
            text = read_text(card_path(root, kind, entry["file"]))
        except (OSError, ValueError) as exc:
            found.append(label + "：" + str(exc))
            continue
        texts[(kind, entry["id"])] = text
        if digest(text) != entry["card_sha256"]:
            found.append(label + " 指纹不一致：" + REBUILD_HINT)
    record("catalog：每张卡都在、不是软链、指纹一致", len(entries), found)

    found, scanned = [], 0
    for kind in ("story", "writing"):
        try:
            cards, scan_errors = scan(root, kind)
        except (OSError, ValueError) as exc:
            found.append(str(exc))
            continue
        found.extend(scan_errors)
        scanned += len(cards) + len(scan_errors)
        listed = {entry["file"] for entry in data[kind]}
        for card in cards:
            if card["file"] not in listed:
                found.append(DIRS[kind].as_posix() + "/" + card["file"] + " 没登记进 catalog：" + REBUILD_HINT)
    record("卡片目录：每张卡都登记了，没有软链", scanned, found)

    found = []
    for entry in data["story"]:
        text = texts.get(("story", entry["id"]))
        if text is None:
            continue
        label = card_label("story", entry)
        status = status_of(text)
        if status is None:
            found.append(label + " 第 3 行不是以“状态：”开头")
        elif status != entry["status"]:
            found.append(label + " 第 3 行的状态和 catalog 不一致：" + REBUILD_HINT)
        found.extend(block_errors(label, text))
        for phrase in STORY_PHRASES:
            if phrase not in text:
                found.append(label + " 缺“" + phrase + "”")
    record("故事卡：第 3 行“状态：”、## 1. 到 ## 5.、准入两组、可删可合并、按材料调整", len(data["story"]), found)

    found = []
    story_ids = {entry["id"] for entry in data["story"]}
    for card_id in SKELETON_CARDS:
        if card_id not in story_ids:
            found.append("故事卡 " + card_id + " 不在 catalog 里")
            continue
        text = texts.get(("story", card_id))
        if text is not None and SKELETON_PHRASE not in text:
            found.append("故事卡 " + card_id + " 缺“" + SKELETON_PHRASE + "”")
    record("只有骨架的故事卡 27、29、31：含“" + SKELETON_PHRASE + "”", len(SKELETON_CARDS), found)

    found = []
    for entry in data["writing"]:
        text = texts.get(("writing", entry["id"]))
        if text is not None:
            found.extend(block_errors(card_label("writing", entry), text))
    record("写法卡：## 1. 到 ## 5.", len(data["writing"]), found)

    found, link_count, index_text = [], 0, None
    try:
        index_text = read_index(root)
    except (OSError, ValueError) as exc:
        found.append(str(exc))
    if index_text is not None:
        index_dir = card_dir(root, "story")
        linked = set()
        for target in LINK.findall(FENCE.sub("", index_text)):
            target = target.strip().strip("<>")
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            link_count += 1
            part = unquote(target).partition("#")[0]
            if not part:
                continue
            if part.startswith(("/", "~", "file:")):
                found.append("index.md 的链接是本机路径：" + target)
                continue
            dest = index_dir / part
            if not dest.exists():
                found.append("index.md 的链接指向不存在的文件：" + target)
                continue
            if dest.resolve() != root.resolve() and root.resolve() not in dest.resolve().parents:
                found.append("index.md 的链接指到技能目录外：" + target)
                continue
            linked.add(dest.resolve())
        for kind, entry in entries:
            if (kind, entry["id"]) in texts and (card_dir(root, kind) / entry["file"]).resolve() not in linked:
                found.append("index.md 没链到" + card_label(kind, entry) + "：" + entry["file"])
        try:
            whole = whole_files(root, index_text)
        except ValueError as exc:
            found.append(str(exc))
            whole = None
        if whole is not None:
            for entry in data["writing"]:
                expected = WHOLE if entry["file"] in whole else PARTIAL
                if entry["use"] != expected:
                    found.append(card_label("writing", entry) + " 按 index.md 所在的表应为“" + expected + "”：" + REBUILD_HINT)
    record("路由目录：链到每张卡、没有断链、写法卡用途和所在的表一致", link_count, found)

    found = []
    named = [(card_label(kind, entry), texts[(kind, entry["id"])]) for kind, entry in entries if (kind, entry["id"]) in texts]
    if index_text is not None:
        named.append(("index.md", index_text))
    for label, text in named:
        if LOCAL_PREFIX in text:
            line = text.count("\n", 0, text.index(LOCAL_PREFIX)) + 1
            found.append(label + " 第 " + str(line) + " 行有本机路径")
    record("本机路径：卡片和 index.md 里没有本机用户目录", len(named), found)

    source_check = "not_run"
    if sources is not None:
        found, lines = source_errors(data, texts, sources)
        record("原页：故事卡里以 > 开头的案例行都能在原页正文里找到", lines, found)
        source_check = {"files": sources["files"], "pages": len(sources["texts"]),
                        "lines": lines, "not_found": len(found)}
    return summary({"story": len(data["story"]), "writing": len(data["writing"])}, source_check)


def changed_cards(old, new):
    """和旧 catalog 比，列出新增、删掉和改过的卡，方便对照审过的是哪几张。"""
    def keyed(data):
        return {(kind, entry["id"]): entry for kind in ("story", "writing") for entry in (data or {}).get(kind, [])}
    before, after = keyed(old), keyed(new)
    def label(key):
        return LABELS[key[0]] + " " + key[1]
    return {"added": [label(k) for k in after if k not in before],
            "removed": [label(k) for k in before if k not in after],
            "changed": [label(k) for k in after if k in before and before[k] != after[k]]}


def rebuild(root=ROOT):
    root = Path(root)
    data = build_catalog(root)
    result = validate(root, catalog=data)
    try:
        old = load_catalog(root)
    except (OSError, ValueError):
        old = None
    result["changed_cards"] = changed_cards(old, data)
    if result["errors"]:
        result["catalog"] = "没有改写：先修好上面的问题，审过后再重建"
        return result
    path = card_dir(root, "story") / CATALOG
    if path.is_symlink():
        raise ValueError("catalog.json 不能是软链")
    content = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    if path.is_file() and path.read_text(encoding="utf-8") == content:
        result["catalog"] = "没有变化"
        return result
    handle, temporary = tempfile.mkstemp(dir=str(path.parent), prefix=".catalog-", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(content)
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    except BaseException:
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise
    result["catalog"] = "已重建：" + (DIRS["story"] / CATALOG).as_posix()
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--rebuild", action="store_true",
                   help="卡片和路由目录审过后，重建 catalog.json 里的编号、名字、状态或用途和指纹")
    p.add_argument("--source-dir", type=Path, nargs="+", action="extend", default=[], metavar="目录或JSON",
                   help="保存的 get_page 原页，目录或 JSON 文件都行，可写几个；只拿来核对，不放进技能目录")
    a = p.parse_args()
    if a.rebuild and a.source_dir:
        p.error("--rebuild 和 --source-dir 分开运行")
    try:
        if a.rebuild:
            result = rebuild()
        else:
            result = validate(ROOT, load_sources(a.source_dir) if a.source_dir else None)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"error": str(exc), "error_count": 1}, ensure_ascii=False, indent=2))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
