"""Orthographic expansion helpers for Japanese historical search terms.

The research harness uses these helpers to keep Shinjitai/Kyujitai expansion
explicit. Expansion is intentionally bounded: it proposes labeled companion
queries, not an unbounded combinatorial cloud.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


# One-to-one pairs used in Japanese colonial-era sources. The first column is
# the postwar/Shinjitai form, the second is the prewar/Kyujitai form.
VARIANT_PAIRS: tuple[tuple[str, str], ...] = (
    ("亜", "亞"),
    ("悪", "惡"),
    ("圧", "壓"),
    ("囲", "圍"),
    ("医", "醫"),
    ("為", "爲"),
    ("壱", "壹"),
    ("稲", "稻"),
    ("飲", "飮"),
    ("隠", "隱"),
    ("栄", "榮"),
    ("営", "營"),
    ("衛", "衞"),
    ("駅", "驛"),
    ("円", "圓"),
    ("塩", "鹽"),
    ("応", "應"),
    ("欧", "歐"),
    ("殴", "毆"),
    ("桜", "櫻"),
    ("奥", "奧"),
    ("横", "橫"),
    ("温", "溫"),
    ("仮", "假"),
    ("価", "價"),
    ("画", "畫"),
    ("会", "會"),
    ("壊", "壞"),
    ("懐", "懷"),
    ("絵", "繪"),
    ("拡", "擴"),
    ("覚", "覺"),
    ("学", "學"),
    ("楽", "樂"),
    ("勧", "勸"),
    ("巻", "卷"),
    ("関", "關"),
    ("歓", "歡"),
    ("観", "觀"),
    ("気", "氣"),
    ("帰", "歸"),
    ("亀", "龜"),
    ("偽", "僞"),
    ("戯", "戲"),
    ("犠", "犧"),
    ("旧", "舊"),
    ("拠", "據"),
    ("挙", "擧"),
    ("峡", "峽"),
    ("挟", "挾"),
    ("狭", "狹"),
    ("郷", "鄕"),
    ("暁", "曉"),
    ("区", "區"),
    ("駆", "驅"),
    ("勲", "勳"),
    ("径", "徑"),
    ("経", "經"),
    ("茎", "莖"),
    ("恵", "惠"),
    ("掲", "揭"),
    ("渓", "溪"),
    ("蛍", "螢"),
    ("軽", "輕"),
    ("継", "繼"),
    ("鶏", "鷄"),
    ("芸", "藝"),
    ("撃", "擊"),
    ("欠", "缺"),
    ("倹", "儉"),
    ("剣", "劍"),
    ("圏", "圈"),
    ("検", "檢"),
    ("権", "權"),
    ("献", "獻"),
    ("県", "縣"),
    ("険", "險"),
    ("顕", "顯"),
    ("験", "驗"),
    ("厳", "嚴"),
    ("広", "廣"),
    ("効", "效"),
    ("恒", "恆"),
    ("鉱", "鑛"),
    ("号", "號"),
    ("国", "國"),
    ("済", "濟"),
    ("斎", "齋"),
    ("剤", "劑"),
    ("雑", "雜"),
    ("参", "參"),
    ("惨", "慘"),
    ("桟", "棧"),
    ("蚕", "蠶"),
    ("賛", "贊"),
    ("残", "殘"),
    ("糸", "絲"),
    ("歯", "齒"),
    ("児", "兒"),
    ("辞", "辭"),
    ("湿", "濕"),
    ("実", "實"),
    ("舎", "舍"),
    ("写", "寫"),
    ("釈", "釋"),
    ("寿", "壽"),
    ("収", "收"),
    ("従", "從"),
    ("渋", "澁"),
    ("獣", "獸"),
    ("縦", "縱"),
    ("粛", "肅"),
    ("処", "處"),
    ("将", "將"),
    ("称", "稱"),
    ("証", "證"),
    ("乗", "乘"),
    ("剰", "剩"),
    ("壌", "壤"),
    ("嬢", "孃"),
    ("条", "條"),
    ("浄", "淨"),
    ("状", "狀"),
    ("畳", "疊"),
    ("譲", "讓"),
    ("醸", "釀"),
    ("触", "觸"),
    ("嘱", "囑"),
    ("真", "眞"),
    ("寝", "寢"),
    ("慎", "愼"),
    ("尽", "盡"),
    ("図", "圖"),
    ("粋", "粹"),
    ("酔", "醉"),
    ("随", "隨"),
    ("髄", "髓"),
    ("数", "數"),
    ("瀬", "瀨"),
    ("声", "聲"),
    ("斉", "齊"),
    ("静", "靜"),
    ("窃", "竊"),
    ("摂", "攝"),
    ("専", "專"),
    ("戦", "戰"),
    ("浅", "淺"),
    ("潜", "潛"),
    ("繊", "纖"),
    ("践", "踐"),
    ("銭", "錢"),
    ("禅", "禪"),
    ("双", "雙"),
    ("壮", "壯"),
    ("争", "爭"),
    ("荘", "莊"),
    ("捜", "搜"),
    ("挿", "插"),
    ("巣", "巢"),
    ("装", "裝"),
    ("総", "總"),
    ("聡", "聰"),
    ("蔵", "藏"),
    ("臓", "臟"),
    ("属", "屬"),
    ("続", "續"),
    ("体", "體"),
    ("対", "對"),
    ("帯", "帶"),
    ("滞", "滯"),
    ("台", "臺"),
    ("滝", "瀧"),
    ("択", "擇"),
    ("沢", "澤"),
    ("担", "擔"),
    ("単", "單"),
    ("胆", "膽"),
    ("団", "團"),
    ("断", "斷"),
    ("弾", "彈"),
    ("遅", "遲"),
    ("痴", "癡"),
    ("虫", "蟲"),
    ("昼", "晝"),
    ("鋳", "鑄"),
    ("庁", "廳"),
    ("徴", "徵"),
    ("聴", "聽"),
    ("勅", "敕"),
    ("鎮", "鎭"),
    ("逓", "遞"),
    ("鉄", "鐵"),
    ("転", "轉"),
    ("点", "點"),
    ("伝", "傳"),
    ("党", "黨"),
    ("盗", "盜"),
    ("灯", "燈"),
    ("当", "當"),
    ("闘", "鬪"),
    ("徳", "德"),
    ("独", "獨"),
    ("読", "讀"),
    ("届", "屆"),
    ("縄", "繩"),
    ("弐", "貳"),
    ("脳", "腦"),
    ("覇", "霸"),
    ("廃", "廢"),
    ("売", "賣"),
    ("麦", "麥"),
    ("発", "發"),
    ("髪", "髮"),
    ("抜", "拔"),
    ("蛮", "蠻"),
    ("浜", "濱"),
    ("払", "拂"),
    ("仏", "佛"),
    ("辺", "邊"),
    ("変", "變"),
    ("弁", "辨"),
    ("歩", "步"),
    ("舗", "舖"),
    ("穂", "穗"),
    ("宝", "寶"),
    ("豊", "豐"),
    ("褒", "襃"),
    ("没", "沒"),
    ("翻", "飜"),
    ("毎", "每"),
    ("万", "萬"),
    ("満", "滿"),
    ("黙", "默"),
    ("薬", "藥"),
    ("訳", "譯"),
    ("予", "豫"),
    ("余", "餘"),
    ("与", "與"),
    ("誉", "譽"),
    ("揺", "搖"),
    ("様", "樣"),
    ("謡", "謠"),
    ("来", "來"),
    ("頼", "賴"),
    ("覧", "覽"),
    ("竜", "龍"),
    ("両", "兩"),
    ("猟", "獵"),
    ("緑", "綠"),
    ("禄", "祿"),
    ("礼", "禮"),
    ("励", "勵"),
    ("隷", "隸"),
    ("霊", "靈"),
    ("齢", "齡"),
    ("暦", "曆"),
    ("歴", "歷"),
    ("恋", "戀"),
    ("炉", "爐"),
    ("労", "勞"),
    ("楼", "樓"),
    ("録", "錄"),
    ("湾", "灣"),
    # Non-Joyo historical variants that recur in this project.
    ("青", "靑"),
    ("錬", "鍊"),
)


NEW_TO_OLD = {new: old for new, old in VARIANT_PAIRS}
OLD_TO_NEW = {old: new for new, old in VARIANT_PAIRS}


@dataclass(frozen=True)
class OrthographicVariant:
    query: str
    label: str
    replacements: tuple[str, ...]


def _replace_chars(text: str, mapping: dict[str, str]) -> tuple[str, tuple[str, ...]]:
    out: list[str] = []
    replacements: list[str] = []
    for char in text:
        replacement = mapping.get(char)
        if replacement:
            out.append(replacement)
            replacements.append(f"{char}->{replacement}")
        else:
            out.append(char)
    return "".join(out), tuple(replacements)


def _single_replacements(text: str) -> Iterable[OrthographicVariant]:
    for index, char in enumerate(text):
        if char in NEW_TO_OLD:
            replacement = NEW_TO_OLD[char]
            yield OrthographicVariant(
                query=f"{text[:index]}{replacement}{text[index + 1:]}",
                label="single_kyujitai_pair",
                replacements=(f"{char}->{replacement}",),
            )
        if char in OLD_TO_NEW:
            replacement = OLD_TO_NEW[char]
            yield OrthographicVariant(
                query=f"{text[:index]}{replacement}{text[index + 1:]}",
                label="single_shinjitai_pair",
                replacements=(f"{char}->{replacement}",),
            )


def expand_orthographic_variants(query: str, max_variants: int = 12) -> list[OrthographicVariant]:
    """Return bounded, labeled Shinjitai/Kyujitai companion queries.

    Scores from queries using these variants should be interpreted per route and
    per query only. The labels are designed to be copied into card `source_query`
    notes when a variant produced the load-bearing hit.
    """
    cleaned = " ".join(str(query or "").split())
    if not cleaned:
        return []

    variants: dict[str, OrthographicVariant] = {
        cleaned: OrthographicVariant(cleaned, "original", ()),
    }

    kyujitai, kyujitai_replacements = _replace_chars(cleaned, NEW_TO_OLD)
    if kyujitai != cleaned:
        variants[kyujitai] = OrthographicVariant(kyujitai, "kyujitai_maximal", kyujitai_replacements)

    shinjitai, shinjitai_replacements = _replace_chars(cleaned, OLD_TO_NEW)
    if shinjitai != cleaned:
        variants[shinjitai] = OrthographicVariant(shinjitai, "shinjitai_maximal", shinjitai_replacements)

    for variant in _single_replacements(cleaned):
        if len(variants) >= max_variants:
            break
        variants.setdefault(variant.query, variant)

    return list(variants.values())[:max_variants]


def variants_as_dicts(query: str, max_variants: int = 12) -> list[dict[str, object]]:
    return [
        {
            "query": variant.query,
            "label": variant.label,
            "replacements": list(variant.replacements),
        }
        for variant in expand_orthographic_variants(query, max_variants=max_variants)
    ]
