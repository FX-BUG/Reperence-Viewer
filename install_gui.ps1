Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$DIR        = Split-Path -Parent $MyInvocation.MyCommand.Definition
$PYTHON_DIR = Join-Path $DIR '_app\python'
$PYTHON_EXE = Join-Path $PYTHON_DIR 'python.exe'
$PYTHON_URL = 'https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip'
$PIP_URL    = 'https://bootstrap.pypa.io/get-pip.py'

# Already installed
if (Test-Path $PYTHON_EXE) {
    [System.Windows.Forms.MessageBox]::Show(
        "RPview is already installed.`nUse RPview shortcut to launch.",
        "RPview Setup",
        [System.Windows.Forms.MessageBoxButtons]::OK,
        [System.Windows.Forms.MessageBoxIcon]::Information
    ) | Out-Null
    exit 0
}

# Color palette — desaturated
$C_BG      = [System.Drawing.Color]::FromArgb(18,  18,  18 )   # near-black
$C_SURFACE = [System.Drawing.Color]::FromArgb(24,  24,  24 )   # dark surface
$C_OVERLAY = [System.Drawing.Color]::FromArgb(40,  40,  40 )   # subtle border
$C_TEXT    = [System.Drawing.Color]::FromArgb(220, 220, 220)   # primary text
$C_SUBTEXT = [System.Drawing.Color]::FromArgb(120, 120, 120)   # secondary text
$C_FILL    = [System.Drawing.Color]::FromArgb(180, 180, 180)   # progress fill
$C_SUCCESS = [System.Drawing.Color]::FromArgb(190, 190, 190)   # done state
$C_ERROR   = [System.Drawing.Color]::FromArgb(160, 100, 100)   # error (muted red)
$C_BTN_BG  = [System.Drawing.Color]::FromArgb(55,  55,  55 )
$C_BTN_FG  = [System.Drawing.Color]::FromArgb(210, 210, 210)

# Form
$form = New-Object System.Windows.Forms.Form
$form.Text            = 'RPview Setup'
$form.ClientSize      = New-Object System.Drawing.Size(460, 270)
$form.StartPosition   = 'CenterScreen'
$form.FormBorderStyle = 'FixedSingle'
$form.MaximizeBox     = $false
$form.BackColor       = $C_SURFACE
$form.Font            = New-Object System.Drawing.Font('Segoe UI', 10)

# Header panel
$pnlHeader = New-Object System.Windows.Forms.Panel
$pnlHeader.Size      = New-Object System.Drawing.Size(460, 90)
$pnlHeader.Location  = New-Object System.Drawing.Point(0, 0)
$pnlHeader.BackColor = $C_BG
$form.Controls.Add($pnlHeader)

$lbTitle = New-Object System.Windows.Forms.Label
$lbTitle.Text      = 'RPview'
$lbTitle.Font      = New-Object System.Drawing.Font('Segoe UI', 22, [System.Drawing.FontStyle]::Bold)
$lbTitle.ForeColor = $C_TEXT
$lbTitle.AutoSize  = $true
$lbTitle.Location  = New-Object System.Drawing.Point(30, 16)
$pnlHeader.Controls.Add($lbTitle)

$lbSub = New-Object System.Windows.Forms.Label
$lbSub.Text      = 'Reference Viewer  -  Setup'
$lbSub.Font      = New-Object System.Drawing.Font('Segoe UI', 9)
$lbSub.ForeColor = $C_SUBTEXT
$lbSub.AutoSize  = $true
$lbSub.Location  = New-Object System.Drawing.Point(33, 60)
$pnlHeader.Controls.Add($lbSub)

# Separator
$sep = New-Object System.Windows.Forms.Panel
$sep.Size      = New-Object System.Drawing.Size(460, 1)
$sep.Location  = New-Object System.Drawing.Point(0, 90)
$sep.BackColor = $C_OVERLAY
$form.Controls.Add($sep)

# Status label
$lbStatus = New-Object System.Windows.Forms.Label
$lbStatus.Text      = 'Initializing...'
$lbStatus.Font      = New-Object System.Drawing.Font('Segoe UI', 9)
$lbStatus.ForeColor = $C_TEXT
$lbStatus.Size      = New-Object System.Drawing.Size(400, 22)
$lbStatus.Location  = New-Object System.Drawing.Point(30, 112)
$form.Controls.Add($lbStatus)

# Progress track
$pgBg = New-Object System.Windows.Forms.Panel
$pgBg.Size      = New-Object System.Drawing.Size(400, 3)
$pgBg.Location  = New-Object System.Drawing.Point(30, 146)
$pgBg.BackColor = $C_OVERLAY
$form.Controls.Add($pgBg)

# Progress fill
$pgFill = New-Object System.Windows.Forms.Panel
$pgFill.Size      = New-Object System.Drawing.Size(0, 3)
$pgFill.Location  = New-Object System.Drawing.Point(0, 0)
$pgFill.BackColor = $C_FILL
$pgBg.Controls.Add($pgFill)

# Percent label
$lbPct = New-Object System.Windows.Forms.Label
$lbPct.Text      = '0%'
$lbPct.Font      = New-Object System.Drawing.Font('Segoe UI', 8)
$lbPct.ForeColor = $C_SUBTEXT
$lbPct.AutoSize  = $true
$lbPct.Location  = New-Object System.Drawing.Point(30, 158)
$form.Controls.Add($lbPct)

# Spinner label
$lbSpin = New-Object System.Windows.Forms.Label
$lbSpin.Text      = '|'
$lbSpin.Font      = New-Object System.Drawing.Font('Consolas', 10)
$lbSpin.ForeColor = $C_SUBTEXT
$lbSpin.AutoSize  = $true
$lbSpin.Location  = New-Object System.Drawing.Point(415, 110)
$form.Controls.Add($lbSpin)

# Done / error message
$lbDone = New-Object System.Windows.Forms.Label
$lbDone.Text      = ''
$lbDone.Font      = New-Object System.Drawing.Font('Segoe UI', 9)
$lbDone.ForeColor = $C_SUCCESS
$lbDone.Size      = New-Object System.Drawing.Size(380, 22)
$lbDone.Location  = New-Object System.Drawing.Point(30, 200)
$form.Controls.Add($lbDone)

# Close button
$btnClose = New-Object System.Windows.Forms.Button
$btnClose.Text      = 'Close'
$btnClose.Size      = New-Object System.Drawing.Size(80, 28)
$btnClose.Location  = New-Object System.Drawing.Point(350, 230)
$btnClose.FlatStyle = 'Flat'
$btnClose.BackColor = $C_BTN_BG
$btnClose.ForeColor = $C_BTN_FG
$btnClose.Font      = New-Object System.Drawing.Font('Segoe UI', 9)
$btnClose.FlatAppearance.BorderSize  = 1
$btnClose.FlatAppearance.BorderColor = $C_OVERLAY
$btnClose.Cursor    = [System.Windows.Forms.Cursors]::Hand
$btnClose.Visible   = $false
$btnClose.Add_Click({ $form.Close() })
$form.Controls.Add($btnClose)

# Sync hashtable
$sync = [hashtable]::Synchronized(@{
    Progress = 0
    Status   = 'Initializing...'
    Done     = $false
    Success  = $false
    Error    = ''
    SpinIdx  = 0
})

# Background runspace
$rs = [runspacefactory]::CreateRunspace()
$rs.ApartmentState = 'STA'
$rs.ThreadOptions  = 'ReuseThread'
$rs.Open()
$rs.SessionStateProxy.SetVariable('sync',       $sync)
$rs.SessionStateProxy.SetVariable('PYTHON_DIR', $PYTHON_DIR)
$rs.SessionStateProxy.SetVariable('PYTHON_EXE', $PYTHON_EXE)
$rs.SessionStateProxy.SetVariable('PYTHON_URL', $PYTHON_URL)
$rs.SessionStateProxy.SetVariable('PIP_URL',    $PIP_URL)

$installScript = {
    try {
        $sync.Status   = 'Creating directories...'
        $sync.Progress = 5
        [System.IO.Directory]::CreateDirectory($PYTHON_DIR) | Out-Null

        $sync.Status   = 'Downloading Python 3.11  (~11 MB)...'
        $sync.Progress = 10
        $zipPath = Join-Path $PYTHON_DIR 'python.zip'
        $wc = New-Object System.Net.WebClient
        $wc.DownloadFile($PYTHON_URL, $zipPath)
        $sync.Progress = 42

        $sync.Status   = 'Extracting Python...'
        $sync.Progress = 45
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        [System.IO.Compression.ZipFile]::ExtractToDirectory($zipPath, $PYTHON_DIR)
        Remove-Item $zipPath -Force
        $sync.Progress = 55

        $sync.Status   = 'Configuring environment...'
        $sync.Progress = 57
        $pthFile = Join-Path $PYTHON_DIR 'python311._pth'
        "import site`npython311.zip`n." | Set-Content $pthFile -Encoding ASCII

        $sync.Status   = 'Downloading pip...'
        $sync.Progress = 60
        $getPipPath = Join-Path $PYTHON_DIR 'get-pip.py'
        $wc.DownloadFile($PIP_URL, $getPipPath)
        $sync.Progress = 65

        $sync.Status   = 'Installing pip...'
        $sync.Progress = 67
        & $PYTHON_EXE $getPipPath --no-warn-script-location 2>&1 | Out-Null
        Remove-Item $getPipPath -Force
        $sync.Progress = 73

        $packages = @(
            @{ Name = 'PyQt5';         End = 83  },
            @{ Name = 'opencv-python'; End = 90  },
            @{ Name = 'numpy';         End = 94  },
            @{ Name = 'Pillow';        End = 97  },
            @{ Name = 'psutil';        End = 100 }
        )
        foreach ($pkg in $packages) {
            $sync.Status = "Installing $($pkg.Name)..."
            & $PYTHON_EXE -m pip install $pkg.Name --no-warn-script-location -q 2>&1 | Out-Null
            $sync.Progress = $pkg.End
        }

        $sync.Status  = 'Done.'
        $sync.Success = $true

    } catch {
        $sync.Error  = $_.Exception.Message
        $sync.Status = 'Installation failed.'
    } finally {
        $sync.Done = $true
    }
}

$ps = [powershell]::Create()
$ps.Runspace = $rs
$ps.AddScript($installScript) | Out-Null
$handle = $ps.BeginInvoke()

# UI update timer
$spinChars = [string[]]@('|', '/', '-', '\')

$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 120
$timer.Add_Tick({
    $pct = [int]$sync.Progress
    $lbStatus.Text = $sync.Status
    $lbPct.Text    = "$pct%"
    $fillW = [int](400 * $pct / 100)
    if ($fillW -gt 400) { $fillW = 400 }
    $pgFill.Width = $fillW

    $lbSpin.Text = $spinChars[$sync.SpinIdx % 4]
    $sync.SpinIdx++

    if ($sync.Done) {
        $timer.Stop()
        $lbSpin.Visible = $false
        $ps.EndInvoke($handle) | Out-Null
        $rs.Close()

        if ($sync.Success) {
            $pgFill.BackColor           = $C_SUCCESS
            $lbDone.ForeColor           = $C_SUCCESS
            $lbDone.Text                = 'Installation complete. Use RPview shortcut to launch.'
            $btnClose.BackColor         = [System.Drawing.Color]::FromArgb(60, 120, 200)
            $btnClose.ForeColor         = [System.Drawing.Color]::FromArgb(240, 240, 240)
            $btnClose.FlatAppearance.BorderSize = 0

            # --- Post-install: move all files into _app ---
            try {
                $appDir = Join-Path $DIR '_app'

                # Move remaining files into _app
                foreach ($f in @('RPview.bat', 'gif_ref_viewer.py', 'install_gui.ps1', 'Setup.bat', 'README.md')) {
                    $src = Join-Path $DIR $f
                    $dst = Join-Path $appDir $f
                    if ((Test-Path $src) -and -not (Test-Path $dst)) {
                        Move-Item $src $dst -Force
                    }
                }

                # Create shortcut at root
                $wsh = New-Object -ComObject WScript.Shell
                $lnk = $wsh.CreateShortcut((Join-Path $DIR 'RPview.lnk'))
                $lnk.TargetPath       = Join-Path $appDir 'RPview.bat'
                $lnk.WorkingDirectory = $appDir
                # Icon: image viewer icon from Windows system library
                $lnk.IconLocation     = "$env:SystemRoot\system32\imageres.dll, 335"
                $lnk.Save()
            } catch { <# silently ignore post-install errors #> }
        } else {
            $pgFill.BackColor     = $C_ERROR
            $lbDone.ForeColor     = $C_ERROR
            $lbDone.Text          = "Error: $($sync.Error)"
            $btnClose.BackColor   = [System.Drawing.Color]::FromArgb(70, 40, 40)
        }
        $btnClose.Visible = $true
    }
})
$timer.Start()

$form.Add_FormClosing({ $timer.Stop() })
[System.Windows.Forms.Application]::Run($form)
