#include "../../vendor/helper/native/local_file_path.h"
#include <cassert>
#include <string>

int main() {
    const wchar_t* valid[] = {L"C:\\folder\\file.txt", L"d:/folder/file.silk",
                             L"\\\\?\\D:\\folder with spaces\\file.silk"};
    for (const auto path : valid) assert(local_drive_file_path(path));
    const wchar_t* invalid[] = {L"", L"C:", L"C:relative", L"relative.txt",
        L"\\\\server\\share\\file", L"\\\\?\\UNC\\server\\share\\file",
        L"\\\\.\\C:\\file", L"\\\\?\\GLOBALROOT\\Device\\file",
        L"\\\\?\\Volume{test}\\file", L"\\\\?\\1:\\file", L"\\\\?\\C:/file",
        L"https://example.test/file", L"C:\\file:stream", L"\\\\?\\C:\\file:stream"};
    assert(!local_drive_file_path(nullptr));
    for (const auto path : invalid) assert(!local_drive_file_path(path));
    const std::wstring long_path = L"\\\\?\\C:\\" + std::wstring(400, L'x');
    assert(local_drive_file_path(long_path.c_str()) == long_path.c_str() + 4);
    assert(long_path.size() > 260);
}
