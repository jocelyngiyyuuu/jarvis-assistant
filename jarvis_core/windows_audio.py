"""Build and parse read-only/safe Windows Core Audio PowerShell commands."""

import base64
import gzip
import re


_CORE_AUDIO_CSHARP = r"""
using System;
using System.Runtime.InteropServices;

[Guid("5CDF2C82-841E-4546-9722-0CF74078229A")]
[InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IAudioEndpointVolume {
    [PreserveSig] int RegisterControlChangeNotify(IntPtr notify);
    [PreserveSig] int UnregisterControlChangeNotify(IntPtr notify);
    [PreserveSig] int GetChannelCount(out uint count);
    [PreserveSig] int SetMasterVolumeLevel(float levelDb, Guid context);
    [PreserveSig] int SetMasterVolumeLevelScalar(float level, Guid context);
    [PreserveSig] int GetMasterVolumeLevel(out float levelDb);
    [PreserveSig] int GetMasterVolumeLevelScalar(out float level);
    [PreserveSig] int SetChannelVolumeLevel(uint channel, float levelDb, Guid context);
    [PreserveSig] int SetChannelVolumeLevelScalar(uint channel, float level, Guid context);
    [PreserveSig] int GetChannelVolumeLevel(uint channel, out float levelDb);
    [PreserveSig] int GetChannelVolumeLevelScalar(uint channel, out float level);
    [PreserveSig] int SetMute([MarshalAs(UnmanagedType.Bool)] bool muted, Guid context);
    [PreserveSig] int GetMute(out bool muted);
    [PreserveSig] int GetVolumeStepInfo(out uint step, out uint stepCount);
    [PreserveSig] int VolumeStepUp(Guid context);
    [PreserveSig] int VolumeStepDown(Guid context);
    [PreserveSig] int QueryHardwareSupport(out uint mask);
    [PreserveSig] int GetVolumeRange(out float minDb, out float maxDb, out float incrementDb);
}

[Guid("D666063F-1587-4E43-81F1-B948E807363F")]
[InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDevice {
    [PreserveSig] int Activate(ref Guid iid, int clsCtx, IntPtr activationParams,
        [MarshalAs(UnmanagedType.IUnknown)] out object endpoint);
}

[Guid("A95664D2-9614-4F35-A746-DE8DB63617E6")]
[InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IMMDeviceEnumerator {
    [PreserveSig] int EnumAudioEndpoints(int flow, uint stateMask, out IntPtr devices);
    [PreserveSig] int GetDefaultAudioEndpoint(int flow, int role, out IMMDevice endpoint);
}

[ComImport]
[Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")]
class MMDeviceEnumerator {}

public static class JarvisPcAudio {
    static IAudioEndpointVolume Endpoint() {
        var enumerator = (IMMDeviceEnumerator)(new MMDeviceEnumerator());
        IMMDevice device;
        Marshal.ThrowExceptionForHR(enumerator.GetDefaultAudioEndpoint(0, 1, out device));
        Guid iid = typeof(IAudioEndpointVolume).GUID;
        object endpoint;
        Marshal.ThrowExceptionForHR(device.Activate(ref iid, 23, IntPtr.Zero, out endpoint));
        return (IAudioEndpointVolume)endpoint;
    }

    public static int GetPercent() {
        float level;
        Marshal.ThrowExceptionForHR(Endpoint().GetMasterVolumeLevelScalar(out level));
        return Math.Max(0, Math.Min(100, (int)Math.Round(level * 100.0f)));
    }

    public static void SetPercent(int percent) {
        float level = Math.Max(0, Math.Min(100, percent)) / 100.0f;
        Marshal.ThrowExceptionForHR(Endpoint().SetMasterVolumeLevelScalar(level, Guid.Empty));
        Marshal.ThrowExceptionForHR(Endpoint().SetMute(false, Guid.Empty));
    }

    public static bool GetMuted() {
        bool muted;
        Marshal.ThrowExceptionForHR(Endpoint().GetMute(out muted));
        return muted;
    }

    public static void SetMuted(bool muted) {
        Marshal.ThrowExceptionForHR(Endpoint().SetMute(muted, Guid.Empty));
    }
}
"""


def build_windows_audio_command(action, value=None):
    """Return a compressed, validated PowerShell command below cmd.exe limits."""
    action = str(action).strip().lower()
    if action not in {"get", "set", "change", "mute", "unmute"}:
        raise ValueError("thao tác âm lượng PC không hợp lệ")

    numeric_value = None
    if action in {"set", "change"}:
        try:
            numeric_value = int(value)
        except (TypeError, ValueError) as error:
            raise ValueError("mức âm lượng PC không hợp lệ") from error
        if action == "set":
            numeric_value = max(0, min(100, numeric_value))
        elif not -100 <= numeric_value <= 100:
            raise ValueError("mức thay đổi âm lượng PC phải từ -100 đến 100")

    lines = [
        "$ErrorActionPreference = 'Stop'",
        f"Add-Type -TypeDefinition @'\n{_CORE_AUDIO_CSHARP}\n'@",
        "$before = [JarvisPcAudio]::GetPercent()",
    ]
    if action == "set":
        lines.append(f"[JarvisPcAudio]::SetPercent({numeric_value})")
    elif action == "change":
        lines.extend([
            f"$target = [Math]::Max(0, [Math]::Min(100, $before + ({numeric_value})))",
            "[JarvisPcAudio]::SetPercent($target)",
        ])
    elif action == "mute":
        lines.append("[JarvisPcAudio]::SetMuted($true)")
    elif action == "unmute":
        lines.append("[JarvisPcAudio]::SetMuted($false)")
    lines.extend([
        "$after = [JarvisPcAudio]::GetPercent()",
        "$muted = [JarvisPcAudio]::GetMuted()",
        "Write-Output ('BEFORE=' + $before)",
        "Write-Output ('VOLUME=' + $after)",
        "Write-Output ('MUTED=' + $muted)",
    ])
    script = "\n".join(lines)
    payload = base64.b64encode(
        gzip.compress(script.encode("utf-8"), compresslevel=9, mtime=0)
    ).decode("ascii")
    bootstrap = (
        f"$b=[Convert]::FromBase64String('{payload}');"
        "$m=[IO.MemoryStream]::new();"
        "$m.Write($b,0,$b.Length);$m.Position=0;"
        "$g=[IO.Compression.GzipStream]::new($m,[IO.Compression.CompressionMode]::Decompress);"
        "$r=[IO.StreamReader]::new($g);"
        "& ([ScriptBlock]::Create($r.ReadToEnd()))"
    )
    command = [
        "powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive",
        # Windows OpenSSH thường chạy qua cmd.exe; bao toàn bộ script để các
        # dấu ngoặc/semicolon không bị cmd diễn giải trước PowerShell.
        "-ExecutionPolicy", "Bypass", "-Command", f'"{bootstrap}"',
    ]
    return command


def parse_windows_audio_output(output):
    """Parse the small key/value response emitted by the PowerShell script."""
    values = {}
    for line in str(output or "").splitlines():
        match = re.fullmatch(r"(BEFORE|VOLUME|MUTED)=(.+)", line.strip(), re.IGNORECASE)
        if match:
            values[match.group(1).upper()] = match.group(2).strip()
    if "VOLUME" not in values:
        raise ValueError("Windows không trả về mức âm lượng")
    try:
        volume = max(0, min(100, int(values["VOLUME"])))
        before = max(0, min(100, int(values.get("BEFORE", volume))))
    except ValueError as error:
        raise ValueError("Windows trả về mức âm lượng không hợp lệ") from error
    muted_text = values.get("MUTED", "false").casefold()
    if muted_text not in {"true", "false"}:
        raise ValueError("Windows trả về trạng thái tắt tiếng không hợp lệ")
    return {"before": before, "volume": volume, "muted": muted_text == "true"}
