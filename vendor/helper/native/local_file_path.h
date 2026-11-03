#pragma once
#include <cwchar>

// Inspect the local drive spelling without changing the path passed to Win32 I/O.
static const wchar_t* local_drive_file_path(const wchar_t* path) {
    if (!path) return nullptr;
    const bool extended = std::wcsncmp(path, L"\\\\?\\", 4) == 0;
    const wchar_t* drive = extended ? path + 4 : path;
    if (std::wcslen(drive) < 3 ||
        !((drive[0] >= L'A' && drive[0] <= L'Z') ||
          (drive[0] >= L'a' && drive[0] <= L'z')) ||
        drive[1] != L':' ||
        (drive[2] != L'\\' && (extended || drive[2] != L'/')) ||
        std::wcschr(drive + 2, L':')) return nullptr;
    return drive;
}
