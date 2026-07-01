import concurrent.futures
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from ..shared import *
from .contracts import ClassificationStrategy, RemoteClassificationSource
from .core import ClassifierCore
from .models import ClassificationOptions, RemoteResolutionResult
from .services import DefaultClassificationStrategy


class ClassificationPipeline:
    """并发筛选执行器，负责编排本地判定和远程回退来源。"""

    def __init__(self, classifier: ClassifierCore, strategy: Optional[ClassificationStrategy] = None):
        self.classifier = classifier
        self.strategy = strategy or DefaultClassificationStrategy(classifier)

    def classify_jars_parallel(
        self,
        jar_files: Sequence[Path],
        options: ClassificationOptions,
        progress_callback: Optional[Callable[[int, int, Path], None]] = None,
        result_callback: Optional[Callable[[int, int, Path, Dict[str, Any]], None]] = None,
    ) -> List[Dict[str, Any]]:
        # 这层不关心某个站点怎么查，只关心“先本地，再按策略串联远程来源”。
        total = len(jar_files)
        if total <= 0:
            return []

        worker_count = get_classification_worker_count(total)
        remote_limits = self._build_remote_limits(total)
        results: List[Optional[Dict[str, Any]]] = [None] * total
        completed = 0
        done_event = threading.Event()
        results_lock = threading.Lock()

        remote_queues = {
            "modrinth": _RemoteGate(max_workers=worker_count),
            "mcmod": _RemoteGate(max_workers=remote_limits["mcmod"]),
            "curseforge": _RemoteGate(max_workers=remote_limits["curseforge"]),
        }

        def finish_row(index: int, jar: Path, row: Dict[str, Any]) -> None:
            nonlocal completed
            with results_lock:
                results[index] = row
                completed += 1
                done_count = completed
            if progress_callback:
                progress_callback(done_count, total, jar)
            if result_callback:
                result_callback(done_count, total, jar, row)
            if done_count >= total:
                done_event.set()

        def finish_classification(index: int, jar: Path, meta: ModMeta, classification: Classification) -> None:
            finish_row(index, jar, build_mod_result_row(jar, meta, classification))

        def run_supplemental_chain(jar: Path, meta: ModMeta) -> Optional[Classification]:
            for source in self.strategy.get_supplemental_sources(options):
                classification = source.lookup(jar, meta)
                if classification and classification.category != "unknown":
                    return classification
            return None

        def run_remote_chain(index: int, jar: Path, meta: ModMeta, local: Classification) -> None:
            remote_results: List[RemoteResolutionResult] = []
            try:
                supplemental = run_supplemental_chain(jar, meta)
                if supplemental and supplemental.category != "unknown":
                    finish_classification(index, jar, meta, supplemental)
                    return

                # 远程来源顺序不写死在这里，而是交给 strategy 决定。
                for source in self.strategy.get_remote_sources(options):
                    classification = self._run_remote_source(source, remote_queues, meta)
                    if classification and classification.category != "unknown":
                        finish_classification(index, jar, meta, classification)
                        return
                    remote_results.append(
                        RemoteResolutionResult(
                            source_name=source.name,
                            preserve_unknown=source.preserve_unknown_result,
                            classification=classification,
                        )
                    )

                final_classification = self.strategy.choose_fallback(local, remote_results)
                finish_classification(index, jar, meta, final_classification)
            except Exception as exc:
                finish_row(index, jar, build_mod_error_row(jar, str(exc)))

        def run_local(index: int, jar: Path) -> None:
            try:
                meta = self.strategy.metadata_reader.read(jar)
                local = self.strategy.local_classifier.classify(meta)
                if self.strategy.is_local_final(local):
                    finish_classification(index, jar, meta, local)
                    return
                remote_executor.submit(run_remote_chain, index, jar, meta, local)
            except Exception as exc:
                finish_row(index, jar, build_mod_error_row(jar, str(exc)))

        local_executor = concurrent.futures.ThreadPoolExecutor(max_workers=worker_count)
        remote_executor = concurrent.futures.ThreadPoolExecutor(max_workers=worker_count)
        try:
            for index, jar in enumerate(jar_files):
                local_executor.submit(run_local, index, jar)
            done_event.wait()
        finally:
            local_executor.shutdown(wait=True, cancel_futures=False)
            remote_executor.shutdown(wait=True, cancel_futures=False)

        return [row for row in results if row is not None]

    def rerun_unknown_classifications(
        self,
        rows: List[Dict[str, Any]],
        options: ClassificationOptions,
        progress_callback: Optional[Callable[[int, int, Path], None]] = None,
        result_callback: Optional[Callable[[int, int, Path, Dict[str, Any]], None]] = None,
    ) -> int:
        unknown_rows = [
            row
            for row in rows
            if row.get("Category") == "unknown" and isinstance(row.get("Path"), Path)
        ]
        if not unknown_rows:
            return 0

        retry_classifier = ClassifierCore()
        retry_classifier.use_curseforge = options.use_curseforge
        retry_classifier.use_offline_database = options.use_offline_database
        retry_pipeline = ClassificationPipeline(retry_classifier)
        retry_results = retry_pipeline.classify_jars_parallel(
            [row["Path"] for row in unknown_rows],
            options,
            progress_callback=progress_callback,
            result_callback=result_callback,
        )
        retry_map = {str(row["Path"]): row for row in retry_results}
        recovered = 0
        for row in unknown_rows:
            retry_row = retry_map.get(str(row["Path"]))
            if retry_row and retry_row["Category"] != "unknown":
                preserved_final_path = row.get("FinalPath")
                preserved_selection = row.get("SelectedForServer")
                row.update(retry_row)
                if preserved_final_path is not None:
                    row["FinalPath"] = preserved_final_path
                if preserved_selection is not None:
                    row["SelectedForServer"] = preserved_selection
                recovered += 1
        return recovered

    def _build_remote_limits(self, total: int) -> Dict[str, int]:
        return {
            "mcmod": get_mcmod_worker_count(total),
            "curseforge": min(DEFAULT_CF_WORKERS, max(1, total // 2)),
        }

    def _run_remote_source(
        self,
        source: RemoteClassificationSource,
        remote_queues: Dict[str, "_RemoteGate"],
        meta: ModMeta,
    ) -> Optional[Classification]:
        gate = remote_queues.get(source.concurrency_group)
        if gate is None:
            return source.lookup(meta)
        gate.acquire()
        try:
            return source.lookup(meta)
        finally:
            gate.release()


class _RemoteGate:
    """限制单一远程来源的并发，避免站点过载或被限流。"""

    def __init__(self, max_workers: int):
        self.max_workers = max_workers
        self.active = 0
        self.condition = threading.Condition()

    def acquire(self) -> None:
        with self.condition:
            while self.active >= self.max_workers:
                self.condition.wait()
            self.active += 1

    def release(self) -> None:
        with self.condition:
            self.active -= 1
            self.condition.notify_all()


def classify_jars_parallel(
    classifier: "ClassifierCore",
    jar_files: Sequence[Path],
    use_mcmod: bool,
    use_curseforge: bool = False,
    use_offline_database: bool = False,
    progress_callback: Optional[Callable[[int, int, Path], None]] = None,
    result_callback: Optional[Callable[[int, int, Path, Dict[str, Any]], None]] = None,
) -> List[Dict[str, Any]]:
    pipeline = ClassificationPipeline(classifier)
    return pipeline.classify_jars_parallel(
        jar_files,
        ClassificationOptions(
            use_mcmod=use_mcmod,
            use_curseforge=use_curseforge,
            use_offline_database=use_offline_database,
        ),
        progress_callback=progress_callback,
        result_callback=result_callback,
    )


def rerun_unknown_classifications(
    rows: List[Dict[str, Any]],
    use_mcmod: bool,
    use_curseforge: bool = False,
    use_offline_database: bool = False,
    progress_callback: Optional[Callable[[int, int, Path], None]] = None,
    result_callback: Optional[Callable[[int, int, Path, Dict[str, Any]], None]] = None,
) -> int:
    pipeline = ClassificationPipeline(ClassifierCore())
    return pipeline.rerun_unknown_classifications(
        rows,
        ClassificationOptions(
            use_mcmod=use_mcmod,
            use_curseforge=use_curseforge,
            use_offline_database=use_offline_database,
        ),
        progress_callback=progress_callback,
        result_callback=result_callback,
    )
