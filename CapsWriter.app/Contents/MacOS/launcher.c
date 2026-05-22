/*
 * CapsWriter.app Mach-O 启动器
 *
 * 作用：作为合法的 CFBundleExecutable，通过 execv 替换自身为
 *       .venv/bin/python start_client_macos.py，继承同一 PID
 *       和 .app bundle 身份（bundle ID = com.capswriter.client）。
 *
 * 目录关系：
 *   <project_root>/CapsWriter.app/Contents/MacOS/CapsWriter  ← 本二进制
 *   <project_root>/CapsWriter.app/                           ← .app bundle 根
 *   <project_root>/                                          ← 项目根（向上 3 级）
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <mach-o/dyld.h>

int main(int argc, char *argv[]) {
    /* 获取本二进制的完整路径 */
    char exe_path[4096];
    uint32_t size = sizeof(exe_path);
    if (_NSGetExecutablePath(exe_path, &size) != 0) {
        fprintf(stderr, "[CapsWriter.app] 无法获取可执行文件路径\n");
        return 1;
    }

    /* 向上 3 级斜杠找到项目根目录
     * exe_path = .../CapsWriter.app/Contents/MacOS/CapsWriter
     * 去掉 /CapsWriter  → .../CapsWriter.app/Contents/MacOS
     * 去掉 /MacOS       → .../CapsWriter.app/Contents
     * 去掉 /Contents    → .../CapsWriter.app
     * 去掉 /CapsWriter.app → 项目根
     */
    char project_root[4096];
    strncpy(project_root, exe_path, sizeof(project_root) - 1);
    project_root[sizeof(project_root) - 1] = '\0';

    int slashes = 0;
    int len = (int)strlen(project_root);
    for (int i = len - 1; i >= 0; i--) {
        if (project_root[i] == '/') {
            slashes++;
            if (slashes == 4) {
                project_root[i] = '\0';
                break;
            }
        }
    }

    if (slashes < 4) {
        fprintf(stderr, "[CapsWriter.app] 路径层级不足，无法定位项目根: %s\n", exe_path);
        return 1;
    }

    /* 构造 Python 路径和入口脚本路径 */
    char python_path[4096];
    char entry_path[4096];
    snprintf(python_path, sizeof(python_path), "%s/.venv/bin/python", project_root);
    snprintf(entry_path, sizeof(entry_path), "%s/start_client_macos.py", project_root);

    /* 检查文件是否存在 */
    if (access(python_path, X_OK) != 0) {
        fprintf(stderr, "[CapsWriter.app] 未找到 Python: %s\n", python_path);
        return 1;
    }
    if (access(entry_path, R_OK) != 0) {
        fprintf(stderr, "[CapsWriter.app] 未找到入口脚本: %s\n", entry_path);
        return 1;
    }

    /* execv 替换当前进程（继承同一 PID 和 .app bundle 身份） */
    char *new_argv[] = {python_path, entry_path, NULL};
    execv(python_path, new_argv);

    /* execv 成功后不会到这里 */
    perror("[CapsWriter.app] execv 失败");
    return 1;
}
