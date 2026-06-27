from ..application.models import BuildServerRequest, PreparedModScanSource, PreparedServerSource, ScanModsRequest
from ..classifier import ClassifierCore, classify_jars_parallel, rerun_unknown_classifications
from ..server_builder import ServerBuilderCore
from ..shared import *


class LegacyModScanService:
    """把现有筛选实现包装成可替换服务。"""

    def run(
        self,
        source: PreparedModScanSource,
        request: ScanModsRequest,
        emit: Callable[[str, Any], None],
        set_runtime_ref: Callable[[Any], None],
    ) -> None:
        classifier: Optional[ClassifierCore] = None
        try:
            classifier = ClassifierCore()
            classifier.use_curseforge = request.use_curseforge
            classifier.browser_warning_callback = lambda message: emit("warning", message)
            set_runtime_ref(classifier)

            jar_files = sorted(source.mods_path.glob("*.jar"), key=lambda item: item.name.lower())
            if not jar_files:
                raise RuntimeError("所选目录中没有找到 jar 模组。")

            timestamp = time.strftime("%Y%m%d_%H%M%S")
            result_root = source.report_root / f"_分类结果_{timestamp}"
            client_dir = result_root / "纯客户端_已移出"
            unknown_dir = result_root / "无法分类_待人工确认"
            client_dir.mkdir(parents=True, exist_ok=True)
            unknown_dir.mkdir(parents=True, exist_ok=True)

            emit("log", f"开始扫描目录：{source.mods_path}")
            emit("log", f"共发现 {len(jar_files)} 个 jar 模组")
            worker_count = get_classification_worker_count(len(jar_files))
            if len(jar_files) > 1:
                emit("log", f"联网分类使用 {worker_count} 个并发线程")

            first_span = 72 if request.enable_second_pass else 88

            def first_pass_progress(completed: int, total: int, jar: Path) -> None:
                percent = completed / max(total, 1)
                emit("progress", percent * first_span)
                emit("status", f"正在汇总：{jar.name}")

            def first_pass_result(completed: int, total: int, jar: Path, row: Dict[str, Any]) -> None:
                emit("log", f"[{completed}/{total}] {jar.name} -> {row['Category']} | {row['Reason']}")

            results = classify_jars_parallel(
                classifier,
                jar_files,
                request.use_mcmod,
                request.use_curseforge,
                progress_callback=first_pass_progress,
                result_callback=first_pass_result,
            )

            unknown_rows = [row for row in results if row["Category"] == "unknown"]
            if request.enable_second_pass:
                if unknown_rows:
                    classifier.close_browser()
                    retry_total = len(unknown_rows)
                    retry_worker_count = get_classification_worker_count(retry_total)
                    emit("log", f"开始进行 2次筛选：仅重试首轮未确定的 {retry_total} 个模组")
                    if retry_total > 1:
                        emit("log", f"2次筛选使用 {retry_worker_count} 个并发线程")

                    def second_pass_progress(completed: int, total: int, jar: Path) -> None:
                        percent = completed / max(total, 1)
                        emit("progress", 72 + percent * 16)
                        emit("status", f"正在进行 2次筛选：{jar.name}")

                    def second_pass_result(completed: int, total: int, jar: Path, row: Dict[str, Any]) -> None:
                        emit("log", f"[2次筛选 {completed}/{total}] {jar.name} -> {row['Category']} | {row['Reason']}")

                    recovered = rerun_unknown_classifications(
                        results,
                        request.use_mcmod,
                        request.use_curseforge,
                        progress_callback=second_pass_progress,
                        result_callback=second_pass_result,
                    )
                    remaining_unknown = sum(1 for row in results if row["Category"] == "unknown")
                    emit("log", f"2次筛选完成：回补 {recovered} 个，仍待确认 {remaining_unknown} 个")
                else:
                    emit("log", "已开启 2次筛选，但首轮没有 unknown 模组，跳过重试")

            emit("progress", 90)
            emit("status", "正在整理分类结果目录…")
            if not source.allow_file_move and not request.dry_run:
                raise RuntimeError("当前输入源不支持直接移动原始文件，请改用仅试运行。")

            for row in results:
                source_path = row["Path"]
                final_path = str(source_path)
                if row["Category"] == "client-only":
                    target = client_dir / source_path.name
                    final_path = str(target)
                    if not request.dry_run and source_path.exists():
                        shutil.move(str(source_path), str(target))
                elif row["Category"] == "unknown":
                    target = unknown_dir / source_path.name
                    final_path = str(target)
                    if not request.dry_run and source_path.exists():
                        shutil.move(str(source_path), str(target))
                row["FinalPath"] = final_path

            final_unknown_rows = [row for row in results if row["Category"] == "unknown"]
            if final_unknown_rows:
                emit("log", "以下模组在最终结果中仍未自动确认：")
                for row in final_unknown_rows:
                    emit("log", f" - {row['FileName']} | {row['Reason']}")

            json_path = result_root / "分类报告.json"
            csv_path = result_root / "分类报告.csv"
            txt_path = result_root / "分类摘要.txt"

            emit("progress", 96)
            emit("status", "正在写出报告…")
            output_rows = [{key: value for key, value in row.items() if key != "Path"} for row in results]

            json_path.write_text(json.dumps(output_rows, ensure_ascii=False, indent=2), encoding="utf-8")
            write_csv_with_labels(csv_path, output_rows)

            server_keep = sum(1 for item in output_rows if item["Category"] == "server-keep")
            client_only = sum(1 for item in output_rows if item["Category"] == "client-only")
            unknown = sum(1 for item in output_rows if item["Category"] == "unknown")
            summary = "\n".join(
                [
                    f"扫描目录: {source.display_path}",
                    f"执行模式: {'DryRun(不移动文件)' if request.dry_run else '实际移动文件'}",
                    f"服务端保留: {server_keep}",
                    f"纯客户端移出: {client_only}",
                    f"无法分类: {unknown}",
                    f"结果目录: {result_root}",
                    f"JSON 报告: {json_path}",
                    f"CSV 报告: {csv_path}",
                ]
            )
            txt_path.write_text(summary, encoding="utf-8")
            emit(
                "done",
                {
                    "status": f"分类完成：保留 {server_keep}，移出 {client_only}，待确认 {unknown}",
                    "output": str(result_root),
                    "result_dir": result_root,
                    "extra_dir": result_root,
                    "summary": summary,
                },
            )
        except Exception:
            emit("error", traceback.format_exc())
        finally:
            if classifier is not None:
                try:
                    classifier.close_browser()
                except Exception:
                    pass
            set_runtime_ref(None)


class LegacyServerBuildService:
    """把现有一键开服实现包装成可替换服务。"""

    def run(
        self,
        source: PreparedServerSource,
        request: BuildServerRequest,
        emit: Callable[[str, Any], None],
        set_runtime_ref: Callable[[Any], None],
        request_version_choice: Callable[[list], Optional[Any]],
        request_checklist: Callable[[str, str, list], Optional[list]],
    ) -> None:
        classifier: Optional[ClassifierCore] = None
        try:
            classifier = ClassifierCore()
            classifier.use_curseforge = request.use_curseforge
            classifier.browser_warning_callback = lambda message: emit("warning", message)
            set_runtime_ref(classifier)

            builder = ServerBuilderCore(
                classifier=classifier,
                log=lambda message: emit("log", message),
                set_status=lambda message: emit("status", message),
                set_progress=lambda value: emit("progress", value),
                request_version_choice=request_version_choice,
                request_checklist=request_checklist,
                use_mcmod=request.use_mcmod,
                enable_second_pass=request.enable_second_pass,
            )
            result = builder.build_server(source.client_dir, request.output_dir)
            summary = "\n".join(
                [
                    f"客户端目录: {source.display_path}",
                    f"服务端目录: {result['server_root']}",
                    f"日志目录: {result['report_dir']}",
                    f"启动脚本: {result['launch_script']}",
                ]
            )
            emit(
                "done",
                {
                    "status": "服务端制作完成，已通过两次启动验证。",
                    "output": str(result["server_root"]),
                    "result_dir": result["server_root"],
                    "extra_dir": result["report_dir"],
                    "summary": summary,
                },
            )
        except Exception:
            emit("error", traceback.format_exc())
            has_server = False
            try:
                has_server = any(request.output_dir.glob("*.jar")) if request.output_dir.exists() else False
            except Exception:
                pass
            if not has_server and request.output_dir.exists():
                try:
                    shutil.rmtree(request.output_dir, ignore_errors=True)
                except Exception:
                    pass
        finally:
            if classifier is not None:
                try:
                    classifier.close_browser()
                except Exception:
                    pass
            set_runtime_ref(None)
