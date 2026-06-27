from pathlib import Path
from typing import Optional, Protocol, Sequence

from ..shared import Classification, ModMeta
from .models import ClassificationOptions, RemoteResolutionResult


class MetadataReader(Protocol):
    def read(self, jar_path: Path) -> ModMeta:
        ...


class LocalClassifier(Protocol):
    def classify(self, meta: ModMeta) -> Classification:
        ...


class RemoteClassificationSource(Protocol):
    name: str
    concurrency_group: str
    preserve_unknown_result: bool

    def is_enabled(self, options: ClassificationOptions) -> bool:
        ...

    def lookup(self, meta: ModMeta) -> Optional[Classification]:
        ...


class ClassificationStrategy(Protocol):
    metadata_reader: MetadataReader
    local_classifier: LocalClassifier

    def is_local_final(self, classification: Classification) -> bool:
        ...

    def get_remote_sources(self, options: ClassificationOptions) -> Sequence[RemoteClassificationSource]:
        ...

    def choose_fallback(
        self,
        local: Classification,
        remote_results: Sequence[RemoteResolutionResult],
    ) -> Classification:
        ...
