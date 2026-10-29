param([switch]$Send)
$ErrorActionPreference = 'Stop'
Add-Type @'
using System;
using System.Runtime.InteropServices;
public class FileHelperNative {
 [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
 [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
 [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
 [DllImport("user32.dll")] public static extern bool SetCursorPos(int x,int y);
 [DllImport("user32.dll")] public static extern void mouse_event(uint f,uint x,uint y,uint d,UIntPtr e);
}
'@
[FileHelperNative]::SetProcessDPIAware() | Out-Null
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$wxProcess = @(Get-Process Weixin | Where-Object MainWindowHandle -ne 0)
if ($wxProcess.Count -ne 1) { throw '需要一个已打开的微信主窗口。' }
$wxHandle = $wxProcess[0].MainWindowHandle
$wxRoot = [System.Windows.Automation.AutomationElement]::FromHandle($wxHandle)
function Find-Exact($property, $value) {
 $condition = New-Object System.Windows.Automation.PropertyCondition($property,$value)
 return $wxRoot.FindAll([System.Windows.Automation.TreeScope]::Descendants,$condition)
}
function Get-Input {
 $inputs = @(Find-Exact ([System.Windows.Automation.AutomationElement]::ClassNameProperty) 'mmui::ChatInputField')
 if ($inputs.Count -ne 1 -or $inputs[0].Current.Name -ne '文件传输助手') {
  throw '请先手动打开文件传输助手；当前会话未通过检查，不发送。'
 }
 return $inputs[0]
}
$wxInput = Get-Input
$wxValue = [System.Windows.Automation.ValuePattern]$wxInput.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
if ($wxValue.Current.Value) { throw '输入框已有草稿，保留草稿并停止。' }
if (-not $Send) { Write-Output 'READY: 文件传输助手和空输入框已核对；本次未发送。'; exit 0 }
[FileHelperNative]::SetForegroundWindow($wxHandle) | Out-Null
if ([FileHelperNative]::GetForegroundWindow() -ne $wxHandle) { throw '微信未在前台，不发送。' }
$wxText = '[合成通知测试 ' + (Get-Date -Format 'HH:mm:ss') + '] 聊天记忆助手通知通道测试。不含真实聊天内容，请检查手机通知栏。'
$wxValue.SetValue($wxText)
Start-Sleep -Milliseconds 250
$wxInput = Get-Input
$wxValue = [System.Windows.Automation.ValuePattern]$wxInput.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
if ($wxValue.Current.Value -ne $wxText) { throw '测试文字写入未核实，不点击发送。' }
$wxButtons = @(Find-Exact ([System.Windows.Automation.AutomationElement]::NameProperty) '发送' | Where-Object { $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Button })
if ($wxButtons.Count -ne 1 -or -not $wxButtons[0].Current.IsEnabled) { throw '发送按钮不唯一或不可用。' }
$wxRect = $wxButtons[0].Current.BoundingRectangle
$wxPoint = New-Object System.Windows.Point(($wxRect.Left+$wxRect.Right)/2),(($wxRect.Top+$wxRect.Bottom)/2)
$wxHit = [System.Windows.Automation.AutomationElement]::FromPoint($wxPoint)
if ($wxHit.Current.ProcessId -ne $wxProcess[0].Id -or $wxHit.Current.Name -ne '发送') { throw '发送按钮被遮挡，不发送。' }
$null = Get-Input
if ([FileHelperNative]::GetForegroundWindow() -ne $wxHandle) { throw '前台窗口改变，不发送。' }
[FileHelperNative]::SetCursorPos([int]$wxPoint.X,[int]$wxPoint.Y) | Out-Null
[FileHelperNative]::mouse_event(2,0,0,0,[UIntPtr]::Zero)
[FileHelperNative]::mouse_event(4,0,0,0,[UIntPtr]::Zero)
Start-Sleep -Milliseconds 700
$wxValue = [System.Windows.Automation.ValuePattern](Get-Input).GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
$wxMatches = @(Find-Exact ([System.Windows.Automation.AutomationElement]::NameProperty) $wxText)
if (-not $wxValue.Current.Value -and $wxMatches.Count -gt 0) {
 Write-Output "LOCAL_VISIBLE: $wxText"
 Write-Output '手机通知状态待人工确认；本地显示不代表手机已提醒。'
} else {
 Write-Output 'UNCERTAIN: 已点击一次发送，结果未核实；不要自动重试，请检查文件传输助手。'
 exit 2
}
