from ..shared import *


class ClassifierTextMixin:
    def normalize_text(self, text: str, strip_brackets: bool = True) -> str:
        if not text:
            return ""
        text = text.lower()
        if strip_brackets:
            text = re.sub(r"\[[^\]]+\]", "", text)
        text = re.sub(r"[^a-z0-9]+", "", text)
        return text

    def clean_filename_token(self, file_name: str) -> str:
        stem = Path(file_name).stem
        stem = re.sub(r"\[[^\]]+\]", " ", stem)
        stem = re.sub(r"【[^】]+】", " ", stem)
        stem = re.sub(r"\b(mc)?1\.\d+(\.\d+)?\b", " ", stem, flags=re.I)
        stem = re.sub(r"\b(fabric|forge|quilt|neoforge)\b", " ", stem, flags=re.I)
        stem = re.sub(r"\b(v?\d+([._+-]\d+)*([a-z]+\d*)?)\b", " ", stem, flags=re.I)
        stem = re.sub(r"[_\-+.]+", " ", stem)
        stem = re.sub(r"\s+", " ", stem).strip()
        return stem

    def normalize_match_text(self, text: str, strip_brackets: bool = True, keep_cjk: bool = False) -> str:
        if not text:
            return ""
        text = text.lower()
        if strip_brackets:
            text = re.sub(r"\[[^\]]+\]", "", text)
            text = re.sub(r"【[^】]+】", "", text)
        pattern = r"[^a-z0-9\u4e00-\u9fff]+" if keep_cjk else r"[^a-z0-9]+"
        return re.sub(pattern, "", text)

    def expand_query_token(self, value: str) -> List[str]:
        text = str(value or "").strip()
        if not text:
            return []
        variants: List[str] = []

        def add_variant(item: str) -> None:
            cleaned = re.sub(r"\s+", " ", str(item or "").strip())
            if cleaned and cleaned not in variants:
                variants.append(cleaned)

        add_variant(text)
        separator_split = re.sub(r"[_\-.+/]+", " ", text)
        add_variant(separator_split)
        camel_split = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", separator_split)
        camel_split = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", camel_split)
        camel_split = re.sub(r"(?<=[A-Za-z])(?=\d)", " ", camel_split)
        camel_split = re.sub(r"(?<=\d)(?=[A-Za-z])", " ", camel_split)
        add_variant(camel_split)
        if camel_split:
            add_variant(camel_split.replace(" ", ""))
        return variants

    def extract_bracket_tokens(self, file_name: str) -> List[str]:
        stem = Path(file_name).stem
        tokens: List[str] = []
        for pattern in (r"\[([^\]]{1,60})\]", r"【([^】]{1,60})】"):
            for match in re.findall(pattern, stem):
                token = re.sub(r"\s+", " ", str(match or "").strip())
                if token and token not in tokens:
                    tokens.append(token)
        return tokens

    def split_words(self, text: str, keep_cjk: bool = False) -> List[str]:
        value = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", str(text or "").strip())
        value = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", value)
        pattern = r"[a-z0-9\u4e00-\u9fff]+" if keep_cjk else r"[a-z0-9]+"
        return re.findall(pattern, value.lower())

    def normalize_match_word(self, word: str) -> str:
        value = str(word or "").strip().lower()
        if not value:
            return ""
        roman_map = {
            "ii": "2",
            "iii": "3",
            "iv": "4",
            "v": "5",
            "vi": "6",
            "vii": "7",
            "viii": "8",
            "ix": "9",
            "x": "10",
        }
        return roman_map.get(value, value)

    def is_placeholder_value(self, value: str) -> bool:
        text = str(value or "").strip()
        return bool(text and re.fullmatch(r"\$\{[^}]+\}", text))

    def is_generic_query_token(self, value: str) -> bool:
        text = str(value or "").strip().lower()
        return bool(text and text in GENERIC_QUERY_TOKENS)

    def is_meaningful_query_token(self, value: str) -> bool:
        text = re.sub(r"\s+", " ", str(value or "").strip())
        if not text or self.is_placeholder_value(text) or self.is_generic_query_token(text):
            return False
        return any(char.isalnum() for char in text)

    def collect_search_values(self, meta: ModMeta, query: str = "") -> List[str]:
        values = [
            meta.mod_id,
            meta.mod_name,
            query,
            *self.extract_bracket_tokens(meta.file_name),
            self.clean_filename_token(meta.file_name),
        ]
        tokens: List[str] = []
        for value in values:
            cleaned = re.sub(r"\s+", " ", str(value or "").strip())
            if not self.is_meaningful_query_token(cleaned):
                continue
            if cleaned not in tokens:
                tokens.append(cleaned)
        return tokens

    def extract_name_variants(self, text: str) -> List[str]:
        raw = re.sub(r"\s+", " ", str(text or "").strip())
        if not raw:
            return []

        variants: List[str] = []

        def add_variant(value: str) -> None:
            cleaned = re.sub(r"\s+", " ", str(value or "").strip(" -|/,:;")).strip()
            if cleaned and cleaned not in variants:
                variants.append(cleaned)

        add_variant(raw)

        bracket_patterns = (
            r"\(([^()]{1,120})\)",
            r"（([^（）]{1,120})）",
            r"\[([^\[\]]{1,120})\]",
            r"【([^【】]{1,120})】",
        )
        for pattern in bracket_patterns:
            for match in re.findall(pattern, raw):
                add_variant(match)

        stripped = raw
        for pattern in bracket_patterns:
            stripped = re.sub(pattern, " ", stripped)
        add_variant(stripped)

        for part in re.split(r"\s*(?:/|\||｜)\s*", raw):
            add_variant(part)

        return variants

    def build_library_base_keys(self, text: str, keep_cjk: bool = False) -> Dict[str, bool]:
        keys: Dict[str, bool] = {}
        for variant in self.extract_name_variants(text):
            words = [
                self.normalize_match_word(word)
                for word in self.split_words(variant, keep_cjk=keep_cjk)
                if self.normalize_match_word(word)
            ]
            if not words:
                continue

            joined = "".join(words)
            if joined and joined not in keys:
                keys[joined] = False

            trimmed = list(words)
            removed_suffix = False
            while len(trimmed) > 1 and trimmed[-1] in LIBRARY_SUFFIX_TERMS:
                trimmed = trimmed[:-1]
                removed_suffix = True
                joined = "".join(trimmed)
                if joined and len(joined) >= 6:
                    keys[joined] = True

            if removed_suffix and len(trimmed) >= 2:
                joined = "".join(trimmed)
                if joined:
                    keys[joined] = True
        return keys

    def score_library_suffix_match(self, left: str, right: str, keep_cjk: bool = False) -> int:
        left_keys = self.build_library_base_keys(left, keep_cjk=keep_cjk)
        right_keys = self.build_library_base_keys(right, keep_cjk=keep_cjk)
        best = 0
        for key, left_relaxed in left_keys.items():
            right_relaxed = right_keys.get(key)
            if right_relaxed is None or len(key) < 6 or not (left_relaxed or right_relaxed):
                continue
            best = max(best, 126 if len(key) >= 10 else 104)
        return best

    def count_word_matches(self, expected: Sequence[str], actual: Sequence[str], threshold: float = 0.82) -> Tuple[int, int, int]:
        filtered_expected = [item for item in expected if self.normalize_match_word(item)]
        filtered_actual = [item for item in actual if self.normalize_match_word(item)]
        if not filtered_expected or not filtered_actual:
            return 0, 0, 0

        matches = 0
        for expected_word in filtered_expected:
            best = max(self.word_similarity(expected_word, actual_word) for actual_word in filtered_actual)
            if best >= threshold:
                matches += 1
        return matches, len(filtered_expected), max(0, len(filtered_actual) - matches)

    def allows_mcmod_extension(self, expected_words: Sequence[str], actual_words: Sequence[str]) -> bool:
        matches, expected_count, extra_words = self.count_word_matches(expected_words, actual_words)
        if not expected_count or matches < expected_count:
            return False
        allowed_extra = 2 if expected_count >= 4 else 1
        return extra_words <= allowed_extra

    def has_mcmod_subtitle_prefix(self, value: str, title_variant: str) -> bool:
        raw_value = re.sub(r"\s+", " ", str(value or "").strip())
        raw_title = re.sub(r"\s+", " ", str(title_variant or "").strip())
        if not raw_value or not raw_title or self.looks_like_compact_alias(raw_value):
            return False

        lower_value = raw_value.lower()
        lower_title = raw_title.lower()
        if not lower_title.startswith(lower_value):
            return False

        remainder = raw_title[len(raw_value):].lstrip()
        return bool(remainder and remainder[0] in ":：-|/｜")

    def score_word_alignment(self, expected: Sequence[str], actual: Sequence[str], allow_partial: bool = True) -> int:
        matches, expected_count, extra_words = self.count_word_matches(expected, actual)
        if not expected_count:
            return 0

        coverage = matches / expected_count
        if coverage >= 0.999:
            if expected_count <= 2:
                if extra_words == 0:
                    return 110
                if extra_words == 1:
                    return 54
                return 0
            if expected_count == 3:
                return 132 if extra_words <= 1 else 98
            return 136 if extra_words <= 1 else 114

        if not allow_partial:
            return 0

        if expected_count >= 4 and coverage >= 0.75 and extra_words <= 2:
            return 84
        if expected_count == 3 and coverage >= 0.67 and extra_words <= 1:
            return 58
        return 0

    def score_directional_containment(self, key: str, candidate: str) -> int:
        if not key or not candidate or key == candidate or key not in candidate:
            return 0
        if len(key) <= 4:
            return 0

        if candidate.startswith(key):
            remainder = candidate[len(key):]
            return 78 if len(remainder) <= 12 else 52
        if candidate.endswith(key):
            remainder = candidate[:-len(key)]
            return 26 if len(remainder) <= 8 else 12
        return 12

    def get_modrinth_loader_tags(self, hit: dict) -> set:
        values = set()
        for key in ("categories", "display_categories", "loaders"):
            for item in hit.get(key) or []:
                normalized = str(item or "").strip().lower()
                if normalized in LOADER_SEARCH_TOKENS:
                    values.add(normalized)
        return values

    def score_modrinth_loader_alignment(self, meta: ModMeta, hit: dict) -> int:
        if meta.loader == LoaderType.UNKNOWN.value:
            return 0
        loader_tags = self.get_modrinth_loader_tags(hit)
        if not loader_tags:
            return 0
        if meta.loader in loader_tags:
            return 36
        return -120

    def is_confident_modrinth_candidate(self, meta: ModMeta, hit: dict) -> bool:
        search_values = self.collect_search_values(meta)
        search_keys = [self.normalize_text(value) for value in search_values if self.normalize_text(value)]
        norm_slug = self.normalize_text(str(hit.get("slug", "")))
        norm_title = self.normalize_text(str(hit.get("title", "")))
        if any(key in {norm_slug, norm_title} for key in search_keys):
            return True

        remote_variants = self.extract_name_variants(str(hit.get("title", ""))) + self.extract_name_variants(str(hit.get("slug", "")))
        title_words = self.split_words(str(hit.get("title", "")))
        slug_words = self.split_words(str(hit.get("slug", "")))
        title_acronyms = {
            self.normalize_text(item, strip_brackets=False)
            for item in self.extract_acronym_candidates(str(hit.get("title", "")))
        }

        for value in search_values:
            if any(self.score_library_suffix_match(value, variant) for variant in remote_variants):
                return True
            value_words = self.split_words(value)
            title_alignment = self.score_word_alignment(value_words, title_words)
            slug_alignment = self.score_word_alignment(value_words, slug_words)
            if title_alignment >= 110 or slug_alignment >= 96:
                return True
            if title_alignment >= 58 and any(key and key in title_acronyms for key in search_keys):
                return True

            norm_value = self.normalize_text(value)
            if norm_value and norm_title.startswith(norm_value) and len(value_words) >= 2:
                return True
        return False

    def is_confident_mcmod_candidate(self, meta: ModMeta, title: str) -> bool:
        search_values = self.collect_search_values(meta)
        title_variants = self.extract_name_variants(title) or [title]
        title_acronyms = {
            self.normalize_match_text(item, strip_brackets=False, keep_cjk=False)
            for item in self.extract_acronym_candidates(title)
        }
        compact_keys = {
            self.normalize_match_text(value, strip_brackets=False, keep_cjk=False)
            for value in search_values
            if self.looks_like_compact_alias(value)
        }

        descriptive_values = [
            value
            for value in search_values
            if len(self.split_words(value, keep_cjk=True)) >= 3
        ]
        descriptive_match = False
        strong_descriptive_match = False
        for title_variant in title_variants:
            title_words = self.split_words(title_variant, keep_cjk=True)
            for value in descriptive_values:
                value_words = self.split_words(value, keep_cjk=True)
                alignment_score = self.score_word_alignment(value_words, title_words)
                if alignment_score >= 58:
                    descriptive_match = True
                if alignment_score >= 110:
                    strong_descriptive_match = True

        for value in search_values:
            if any(self.score_library_suffix_match(value, variant, keep_cjk=True) for variant in title_variants):
                return True
            if (
                descriptive_match
                and self.normalize_match_text(value, strip_brackets=False, keep_cjk=False) in title_acronyms
            ):
                return True

        for title_variant in title_variants:
            norm_title = self.normalize_match_text(title_variant, strip_brackets=False, keep_cjk=True)
            title_words = self.split_words(title_variant, keep_cjk=True)
            compact_title_words = self.split_words(title_variant, keep_cjk=False)

            for value in search_values:
                norm_value = self.normalize_match_text(value, strip_brackets=False, keep_cjk=True)
                if not norm_value:
                    continue

                value_words = self.split_words(value, keep_cjk=True)
                if norm_title == norm_value:
                    if (
                        len(value_words) >= 2
                        or len(compact_title_words) >= 2
                        or len(norm_value) >= 8
                        or not self.looks_like_compact_alias(value)
                    ):
                        return True

                if (
                    norm_title.startswith(norm_value)
                    and len(value_words) >= 2
                    and (
                        self.allows_mcmod_extension(value_words, title_words)
                        or self.has_mcmod_subtitle_prefix(value, title_variant)
                    )
                    and (not self.looks_like_compact_alias(value) or descriptive_match)
                ):
                    return True

                if self.score_word_alignment(value_words, title_words) >= 110:
                    return True

        full_title_words = self.split_words(title, keep_cjk=True)
        if compact_keys & title_acronyms and (descriptive_match or len(full_title_words) >= 3):
            return True
        return strong_descriptive_match

    def expand_match_word(self, word: str) -> List[str]:
        base = self.normalize_match_word(word)
        if not base or base == "s":
            return []

        variants = {base}
        if base.endswith("ies") and len(base) > 4:
            variants.add(base[:-3] + "y")
        if base.endswith("es") and len(base) > 4:
            variants.add(base[:-2])
        if base.endswith("s") and len(base) > 4:
            variants.add(base[:-1])
        if base.endswith("ing") and len(base) > 5:
            variants.add(base[:-3])
        return [item for item in variants if item]

    def word_similarity(self, left: str, right: str) -> float:
        left_variants = self.expand_match_word(left)
        right_variants = self.expand_match_word(right)
        if not left_variants or not right_variants:
            return 0.0
        best = 0.0
        for left_item in left_variants:
            for right_item in right_variants:
                if left_item == right_item:
                    return 1.0
                best = max(best, difflib.SequenceMatcher(None, left_item, right_item).ratio())
        return best

    def extract_acronym_candidates(self, text: str) -> List[str]:
        raw = str(text or "").strip()
        if not raw:
            return []

        acronyms: List[str] = []
        for token in re.findall(r"\(([A-Z0-9]{2,12})\)", raw):
            cleaned = token.strip()
            if cleaned and cleaned not in acronyms:
                acronyms.append(cleaned)
        for token in re.findall(r"[\[【]([A-Za-z0-9]{2,16})[\]】]", raw):
            cleaned = token.strip()
            if cleaned and cleaned not in acronyms:
                acronyms.append(cleaned)

        words = [item for item in self.split_words(raw) if len(item) > 1 and not item.isdigit()]
        if len(words) >= 2:
            acronym = "".join(item[0] for item in words[:8]).upper()
            if len(acronym) >= 2 and acronym not in acronyms:
                acronyms.append(acronym)
        return acronyms

    def build_query_tokens(self, file_name: str, *values: str) -> List[str]:
        query_tokens: List[str] = []
        for value in (*values, self.clean_filename_token(file_name)):
            if self.is_placeholder_value(value):
                continue
            for variant in self.expand_query_token(value):
                if self.is_meaningful_query_token(variant) and variant not in query_tokens:
                    query_tokens.append(variant)
        return query_tokens

    def build_mcmod_query_tokens(self, meta: ModMeta) -> List[str]:
        query_tokens: List[str] = []
        values = [meta.mod_id, meta.mod_name, *self.extract_bracket_tokens(meta.file_name), self.clean_filename_token(meta.file_name)]
        for value in values:
            if self.is_placeholder_value(value):
                continue
            for variant in self.expand_query_token(value):
                if self.is_meaningful_query_token(variant) and variant not in query_tokens:
                    query_tokens.append(variant)
        return query_tokens

    def collect_unique_queries(self, tokens: Sequence[str], limit: int = 8) -> List[str]:
        queries: List[str] = []
        seen_queries = set()
        for token in tokens:
            query = token.strip()
            if not query or query in seen_queries:
                continue
            seen_queries.add(query)
            queries.append(query)
            if len(queries) >= limit:
                break
        return queries

    def looks_like_compact_alias(self, text: str) -> bool:
        raw = str(text or "").strip()
        if not raw or " " in raw:
            return False
        return bool(
            re.search(r"\d", raw)
            or re.search(r"[A-Z]{2,}", raw)
            or re.search(r"[_-]", raw)
            or (raw.islower() and len(raw) <= 8)
        )

    def extract_page_title(self, html: str) -> str:
        match = re.search(r"<title>(.*?)</title>", html, flags=re.I | re.S)
        if not match:
            return ""
        title = re.sub(r"\s+", " ", match.group(1)).strip()
        title = re.sub(r"\s*\|\s*最大的Minecraft中文MOD百科.*$", "", title)
        title = re.sub(r"\s*[-|｜]\s*MC百科.*$", "", title)
        return title.strip()

    def extract_mcmod_search_results(self, html: str) -> List[Tuple[str, str]]:
        candidates: List[Tuple[str, str]] = []
        seen_links = set()
        for href, raw_title in re.findall(r'<a[^>]+href="([^"]*class/\d+\.html[^"]*)"[^>]*>(.*?)</a>', html, flags=re.I | re.S):
            link = urllib.parse.urljoin("https://www.mcmod.cn", href.strip())
            if not re.match(r"^https?://www\.mcmod\.cn/class/\d+\.html$", link):
                continue
            title = re.sub(r"<.*?>", "", raw_title).strip()
            title = re.sub(r"\s+", " ", title)
            if not title or title.startswith("www.mcmod.cn/class/") or link in seen_links:
                continue
            seen_links.add(link)
            candidates.append((title, link))
        return candidates

    def extract_mcmod_environment(self, html: str) -> str:
        if not html:
            return ""

        text = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", html)
        text = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", text)
        text = re.sub(r"<[^>]+>", " ", text)
        text = text.replace("&nbsp;", " ").replace("&#160;", " ")
        text = re.sub(r"\s+", " ", text).strip()

        for pattern in (
            r"运行环境\s*[:：]\s*(.{1,120}?)(?=\s*(收录时间|编辑次数|最后编辑|最后推荐|模组标签|支持的MC版本|相关链接|Mod作者|总浏览|$))",
            r"运行环境\s*(客户端[^ ]{0,20}\s*,\s*服务端[^ ]{0,20})",
        ):
            match = re.search(pattern, text, flags=re.I)
            if match:
                return re.sub(r"\s+", " ", match.group(1)).strip()
        return ""

    def normalize_entrypoint_name(self, entrypoint_name: str) -> str:
        normalized = str(entrypoint_name or "").strip().lower()
        normalized = re.sub(r"[\s.\-:/]+", "_", normalized)
        normalized = re.sub(r"_+", "_", normalized).strip("_")
        return normalized

    def is_client_only_entrypoint(self, entrypoint_name: str) -> bool:
        normalized = self.normalize_entrypoint_name(entrypoint_name)
        if not normalized:
            return False
        if normalized in CLIENT_ENTRYPOINTS:
            return True

        parts = [part for part in normalized.split("_") if part]
        if not parts:
            return False
        if "main" in parts or "server" in parts:
            return False
        if "client" in parts:
            return True

        part_set = set(parts)
        if {"jei", "plugin"} <= part_set:
            return True
        if "rei" in part_set and "client" in normalized:
            return True
        if "journeymap" in part_set:
            return True
        if any(token in part_set for token in {"modmenu", "emi", "jade", "waila"}):
            return True

        matched_hints = sum(1 for token in CLIENT_ENTRYPOINT_TOKEN_HINTS if token in part_set)
        return matched_hints >= 2

    def score_contained_alias(self, key: str, candidate: str) -> int:
        return self.score_directional_containment(key, candidate)

    def score_modrinth_hit(self, meta: ModMeta, query: str, hit: dict) -> int:
        score = 0
        norm_slug = self.normalize_text(str(hit.get("slug", "")))
        norm_title = self.normalize_text(str(hit.get("title", "")))
        norm_desc = self.normalize_text(str(hit.get("description", "")))
        search_values = self.collect_search_values(meta, query)
        alias_mode = any(self.looks_like_compact_alias(item) for item in search_values)
        search_keys = [self.normalize_text(item) for item in search_values if self.normalize_text(item)]
        remote_variants = self.extract_name_variants(str(hit.get("title", ""))) + self.extract_name_variants(str(hit.get("slug", "")))

        if any(key == norm_slug for key in search_keys):
            score += 180
        if any(key == norm_title for key in search_keys):
            score += 165
        score += max(self.score_contained_alias(key, norm_slug) for key in search_keys) if search_keys else 0
        score += max(self.score_contained_alias(key, norm_title) for key in search_keys) if search_keys else 0
        if any(key in norm_desc for key in search_keys):
            score += 95 if alias_mode else 35
        if any(key in norm_slug and key != norm_slug for key in search_keys):
            score += 16
        if any(key in norm_title and key != norm_title for key in search_keys):
            score += 12

        title_words = self.split_words(str(hit.get("title", "")))
        slug_words = self.split_words(str(hit.get("slug", "")))
        for value in dict.fromkeys(search_values):
            if not value:
                continue
            value_words = self.split_words(value)
            score += self.score_word_alignment(value_words, title_words)
            score += max(0, self.score_word_alignment(value_words, slug_words) - 16)

        acronym_candidates = [
            self.normalize_text(item, strip_brackets=False)
            for item in self.extract_acronym_candidates(str(hit.get("title", "")))
        ]
        if any(key and key in acronym_candidates for key in search_keys):
            score += 155

        library_bonus = 0
        for value in search_values:
            for remote_value in remote_variants:
                library_bonus = max(library_bonus, self.score_library_suffix_match(value, remote_value))
        score += library_bonus

        score += self.score_modrinth_loader_alignment(meta, hit)
        if hit.get("slug"):
            score += 5
        return score

    def score_mcmod_page(self, meta: ModMeta, title: str) -> int:
        score = 0
        title_variants = self.extract_name_variants(title)
        search_values = self.collect_search_values(meta)

        for title_variant in title_variants or [title]:
            norm_title = self.normalize_match_text(title_variant, strip_brackets=False, keep_cjk=True)
            title_words = self.split_words(title_variant, keep_cjk=True)

            for value in dict.fromkeys(item for item in search_values if item):
                norm_value = self.normalize_match_text(value, strip_brackets=False, keep_cjk=True)
                if not norm_value:
                    continue
                if norm_title == norm_value:
                    score = max(score, 180)
                    continue

                value_words = self.split_words(value, keep_cjk=True)
                if (
                    norm_title.startswith(norm_value)
                    and len(value_words) >= 2
                    and (
                        self.allows_mcmod_extension(value_words, title_words)
                        or self.has_mcmod_subtitle_prefix(value, title_variant)
                    )
                ):
                    score = max(score, 112 if len(value_words) <= 2 else 150)
                elif norm_value in norm_title and self.allows_mcmod_extension(value_words, title_words):
                    score = max(score, self.score_directional_containment(norm_value, norm_title))

                alignment_score = self.score_word_alignment(value_words, title_words)
                if alignment_score:
                    score = max(score, alignment_score + (18 if len(value_words) >= 3 else 0))
                library_score = self.score_library_suffix_match(value, title_variant, keep_cjk=True)
                if library_score:
                    score = max(score, library_score + 28)

        acronym_candidates = [
            self.normalize_match_text(item, strip_brackets=False, keep_cjk=False)
            for item in self.extract_acronym_candidates(title)
        ]
        for value in (meta.mod_id, meta.mod_name):
            norm_value = self.normalize_match_text(value, strip_brackets=False, keep_cjk=False)
            if norm_value and norm_value in acronym_candidates:
                score = max(score, 150)
        return score


class ClassifierTextTools(ClassifierTextMixin):
    """无状态文本工具，供元数据、规则和远程来源复用。"""

    pass

