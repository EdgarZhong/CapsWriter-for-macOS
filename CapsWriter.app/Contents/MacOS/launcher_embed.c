/*
 * CapsWriter.app 嵌入式 Python 启动器
 *
 * 与旧版 launcher.c（execv）的区别：
 *   本文件编译链接 libpython3.13，在 C 进程内直接初始化 CPython，
 *   全程不调用 execv。C binary 始终作为主进程存活，
 *   TCC 麦克风归属因此保持为 CapsWriter，而非 Python3。
 *
 * 目录关系：
 *   <project_root>/CapsWriter.app/Contents/MacOS/CapsWriter  ← 本二进制
 *   <project_root>/CapsWriter.app/                           ← .app bundle 根
 *   <project_root>/                                          ← 项目根（向上 4 级）
 *
 * 编译（在项目根目录执行 build_launcher.sh）：
 *   clang -std=c11 -Wall -O2 -arch arm64 \
 *     -I<python_include> \
 *     -DCW_PY_VERSION=\"3.13\" \
 *     launcher_embed.c \
 *     -L.venv/lib -lpython3.13 -Wl,-rpath,@executable_path/../../../.venv/lib \
 *     -o CapsWriter.app/Contents/MacOS/CapsWriter
 */

#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include <ctype.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#ifndef CW_PY_VERSION
#error "CW_PY_VERSION 必须在编译时通过 -D 传入，例如：3.13"
#endif

static void die(const char *msg) {
    fprintf(stderr, "[CapsWriter] 启动失败: %s\n", msg);
    exit(1);
}

static void die_path(const char *msg, const char *path) {
    fprintf(stderr, "[CapsWriter] 启动失败: %s: %s\n", msg, path);
    exit(1);
}

/* 安全拼接路径，超出缓冲区立即终止 */
static void fmt_path(char *out, size_t out_size, const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    int n = vsnprintf(out, out_size, fmt, ap);
    va_end(ap);
    if (n < 0 || (size_t)n >= out_size) {
        die("路径拼接缓冲区溢出");
    }
}

/* 原地去除首尾空白，解析 pyvenv.cfg 这类小配置文件时使用。 */
static char *trim_space(char *s) {
    while (*s && isspace((unsigned char)*s)) {
        s++;
    }
    char *end = s + strlen(s);
    while (end > s && isspace((unsigned char)*(end - 1))) {
        *(--end) = '\0';
    }
    return s;
}

static int has_suffix(const char *s, const char *suffix) {
    size_t s_len = strlen(s);
    size_t suffix_len = strlen(suffix);
    return s_len >= suffix_len && strcmp(s + s_len - suffix_len, suffix) == 0;
}

static void strip_last_component(char *path) {
    char *slash = strrchr(path, '/');
    if (slash == NULL || slash == path) {
        die_path("路径层级不足，无法取父目录", path);
    }
    *slash = '\0';
}

/*
 * 读取 Python base prefix。
 *
 * 这里刻意不使用编译期 -DPY_BASE_PREFIX，也不把构建机的 /Users/xxx 写进 Mach-O。
 * build_launcher.sh 会把当前 .venv 对应的 sys.base_prefix 写入
 * .venv/capswriter-python-prefix；若该文件不存在，则退回解析 pyvenv.cfg 的 home 字段。
 * 这样 clone 到不同用户名、不同 Python 管理器（mise/Homebrew/uv-managed Python）时，
 * 只要在目标机器执行安装或重建，启动器就会读取目标机器自己的环境。
 */
static void resolve_python_base_prefix(const char *project_root,
                                       char *base_prefix,
                                       size_t base_prefix_size) {
    char prefix_file[PATH_MAX];
    fmt_path(prefix_file, sizeof(prefix_file),
             "%s/.venv/capswriter-python-prefix", project_root);

    FILE *fp = fopen(prefix_file, "r");
    if (fp != NULL) {
        char line[PATH_MAX];
        if (fgets(line, sizeof(line), fp) == NULL) {
            fclose(fp);
            die_path("Python prefix 文件为空", prefix_file);
        }
        fclose(fp);

        char *value = trim_space(line);
        if (*value == '\0') {
            die_path("Python prefix 文件为空", prefix_file);
        }
        strlcpy(base_prefix, value, base_prefix_size);
        return;
    }

    char cfg_path[PATH_MAX];
    fmt_path(cfg_path, sizeof(cfg_path), "%s/.venv/pyvenv.cfg", project_root);
    fp = fopen(cfg_path, "r");
    if (fp == NULL) {
        die_path("未找到 .venv/capswriter-python-prefix 或 .venv/pyvenv.cfg，请重新执行 bash install.sh", cfg_path);
    }

    char home_value[PATH_MAX] = {0};
    char line[PATH_MAX + 64];
    while (fgets(line, sizeof(line), fp) != NULL) {
        char *eq = strchr(line, '=');
        if (eq == NULL) {
            continue;
        }
        *eq = '\0';
        char *key = trim_space(line);
        char *value = trim_space(eq + 1);
        if (strcmp(key, "home") == 0 && *value != '\0') {
            strlcpy(home_value, value, sizeof(home_value));
            break;
        }
    }
    fclose(fp);

    if (home_value[0] == '\0') {
        die_path("pyvenv.cfg 缺少 home 字段，请重新执行 bash install.sh", cfg_path);
    }

    char resolved_home[PATH_MAX];
    if (realpath(home_value, resolved_home) == NULL) {
        strlcpy(resolved_home, home_value, sizeof(resolved_home));
    }

    if (has_suffix(resolved_home, "/bin")) {
        strip_last_component(resolved_home);
    }

    strlcpy(base_prefix, resolved_home, base_prefix_size);
}

/* 向 PyConfig.module_search_paths 追加一条路径 */
static PyStatus append_search_path(PyConfig *config, const char *path) {
    wchar_t *wpath = Py_DecodeLocale(path, NULL);
    if (wpath == NULL) return PyStatus_NoMemory();
    PyStatus s = PyWideStringList_Append(&config->module_search_paths, wpath);
    PyMem_RawFree(wpath);
    return s;
}

int main(int argc, char **argv) {
    /* ── 1. 获取本二进制的绝对路径 ── */
    char raw_exe[PATH_MAX];
    uint32_t raw_size = sizeof(raw_exe);
    if (_NSGetExecutablePath(raw_exe, &raw_size) != 0)
        die("可执行文件路径缓冲区太小");

    char exe_path[PATH_MAX];
    if (realpath(raw_exe, exe_path) == NULL)
        die("无法解析可执行文件绝对路径");

    /* ── 2. 向上 4 级斜杠定位项目根 ──
     *  exe_path = .../project/CapsWriter.app/Contents/MacOS/CapsWriter
     *  去掉 /CapsWriter  → .../CapsWriter.app/Contents/MacOS
     *  去掉 /MacOS       → .../CapsWriter.app/Contents
     *  去掉 /Contents    → .../CapsWriter.app
     *  去掉 /CapsWriter.app → 项目根
     */
    char project_root[PATH_MAX];
    strlcpy(project_root, exe_path, sizeof(project_root));
    for (int i = 0; i < 4; i++) {
        char *slash = strrchr(project_root, '/');
        if (slash == NULL) die("路径层级不足，无法定位项目根");
        *slash = '\0';
    }

    /* ── 3. 构造各路径 ── */
    char entry_path[PATH_MAX];   /* start_client_macos.py */
    char venv_site[PATH_MAX];    /* .venv site-packages */
    char base_stdlib[PATH_MAX];  /* Python stdlib */
    char base_dynload[PATH_MAX]; /* Python lib-dynload */
    char base_prefix[PATH_MAX];  /* 目标机器的 Python base prefix */

    resolve_python_base_prefix(project_root, base_prefix, sizeof(base_prefix));

    fmt_path(entry_path,  sizeof(entry_path),
             "%s/start_client_macos.py", project_root);
    fmt_path(venv_site,   sizeof(venv_site),
             "%s/.venv/lib/python" CW_PY_VERSION "/site-packages", project_root);
    fmt_path(base_stdlib, sizeof(base_stdlib),
             "%s/lib/python" CW_PY_VERSION, base_prefix);
    fmt_path(base_dynload, sizeof(base_dynload),
             "%s/lib/python" CW_PY_VERSION "/lib-dynload", base_prefix);

    if (access(entry_path, R_OK) != 0) {
        fprintf(stderr, "[CapsWriter] 未找到入口脚本: %s\n", entry_path);
        return 1;
    }
    if (access(base_stdlib, R_OK) != 0) {
        die_path("Python 标准库路径不可读，请重新执行 bash install.sh", base_stdlib);
    }
    if (access(base_dynload, R_OK) != 0) {
        die_path("Python lib-dynload 路径不可读，请重新执行 bash install.sh", base_dynload);
    }

    /* ── 4. 构造 Python 侧 argv：[exe_path, entry_path, original_args...] ── */
    int py_argc = argc + 1;
    char **py_argv = calloc((size_t)(py_argc + 1), sizeof(char *));
    if (py_argv == NULL) die("内存分配失败");

    py_argv[0] = exe_path;
    py_argv[1] = entry_path;
    for (int i = 1; i < argc; i++) py_argv[i + 1] = argv[i];

    /* ── 5. 初始化 PyConfig ── */
    PyStatus status;
    PyConfig config;
    PyConfig_InitPythonConfig(&config);

    /* 不继承 shell 环境的 PYTHONPATH / PYTHONHOME，避免污染 */
    config.use_environment    = 0;
    config.site_import        = 1;
    config.user_site_directory = 0;
    config.buffered_stdio     = 0;
    config.parse_argv         = 1;

#define CHECK(expr) \
    do { status = (expr); if (PyStatus_Exception(status)) goto on_error; } while (0)

    /* PyConfig_SetBytesArgv 同时触发预初始化，使 Py_DecodeLocale 可用 */
    CHECK(PyConfig_SetBytesArgv(&config, py_argc, py_argv));

    CHECK(PyConfig_SetBytesString(&config, &config.program_name, exe_path));
    CHECK(PyConfig_SetBytesString(&config, &config.executable,   exe_path));

    /* 以目标机器的原始 Python 为 base prefix，venv 只贡献项目依赖。 */
    CHECK(PyConfig_SetBytesString(&config, &config.home,             base_prefix));
    CHECK(PyConfig_SetBytesString(&config, &config.prefix,           base_prefix));
    CHECK(PyConfig_SetBytesString(&config, &config.exec_prefix,      base_prefix));
    CHECK(PyConfig_SetBytesString(&config, &config.base_prefix,      base_prefix));
    CHECK(PyConfig_SetBytesString(&config, &config.base_exec_prefix, base_prefix));

    /* 手动控制 sys.path，顺序：项目根 → stdlib → lib-dynload → venv site-packages */
    config.module_search_paths_set = 1;
    CHECK(append_search_path(&config, project_root));
    CHECK(append_search_path(&config, base_stdlib));
    CHECK(append_search_path(&config, base_dynload));
    CHECK(append_search_path(&config, venv_site));

    CHECK(Py_InitializeFromConfig(&config));

    PyConfig_Clear(&config);
    free(py_argv);

    /* ── 6. 运行脚本，返回退出码 ── */
    return Py_RunMain();

on_error:
    PyConfig_Clear(&config);
    free(py_argv);
    if (PyStatus_IsExit(status)) return status.exitcode;
    Py_ExitStatusException(status);
    return 1;
}
