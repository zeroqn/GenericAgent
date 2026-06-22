from ga_paths import memory_path, temp_path, runtime_path, bbs_files_path, runtime_asset_path
memory_path("ok.txt").write_text("ok", encoding="utf-8")
temp_path("ok.txt").write_text("ok", encoding="utf-8")
runtime_path("ok.txt").write_text("ok", encoding="utf-8")
bbs_files_path("ok.txt").write_text("ok", encoding="utf-8")
runtime_asset_path("tmwd_cdp_bridge", "config.js").write_text("ok", encoding="utf-8")
