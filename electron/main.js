const { app, BrowserWindow, dialog } = require('electron')
const { spawn } = require('child_process')
const path = require('path')
const http = require('http')

const PORT = 8000
const isDev = !app.isPackaged

let backendProcess = null
let mainWindow = null

function getBackendPath() {
  if (isDev) {
    return null // 开发模式：手动启动 uvicorn
  }
  // 生产模式：PyInstaller 打包的可执行文件放在 resources/backend/
  const ext = process.platform === 'win32' ? '.exe' : ''
  return path.join(process.resourcesPath, 'backend', 'backend', `backend${ext}`)
}

function startBackend() {
  if (isDev) {
    console.log('[dev] 请手动启动后端: cd .. && uvicorn main:app --port 8000')
    return
  }

  const backendPath = getBackendPath()
  console.log('[backend] 启动:', backendPath)

  backendProcess = spawn(backendPath, [], {
    cwd: path.join(process.resourcesPath, 'backend', 'backend'),
    env: { ...process.env, PORT: String(PORT) },
    stdio: ['ignore', 'pipe', 'pipe'],
  })

  backendProcess.stdout.on('data', (d) => console.log('[backend]', d.toString().trim()))
  backendProcess.stderr.on('data', (d) => console.error('[backend]', d.toString().trim()))

  backendProcess.on('exit', (code) => {
    console.warn('[backend] 进程退出，code:', code)
  })
}

function waitForBackend(retries = 90) {
  return new Promise((resolve, reject) => {
    let backendExited = false

    // Watch for backend process exit to fail fast
    if (backendProcess) {
      backendProcess.on('exit', (code) => {
        backendExited = true
      })
    }

    const attempt = (n) => {
      // If backend process already exited, don't keep retrying
      if (backendExited) {
        reject(new Error('后端进程已退出，请检查日志'))
        return
      }

      http.get(`http://localhost:${PORT}/api/health`, (res) => {
        if (res.statusCode < 500) resolve()
        else if (n > 0) setTimeout(() => attempt(n - 1), 1000)
        else reject(new Error('后端启动超时（90秒），请检查防火墙或端口占用'))
      }).on('error', () => {
        if (n > 0) setTimeout(() => attempt(n - 1), 1000)
        else reject(new Error('后端启动超时（90秒），请检查防火墙或端口占用'))
      })
    }
    attempt(retries)
  })
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 800,
    title: 'Account Manager',
    webPreferences: {
      contextIsolation: true,
    },
  })

  mainWindow.loadURL(`http://localhost:${PORT}`)
  mainWindow.on('closed', () => { mainWindow = null })
}

app.whenReady().then(async () => {
  startBackend()

  try {
    await waitForBackend()
  } catch (err) {
    dialog.showErrorBox('启动失败', err.message)
    app.quit()
    return
  }

  createWindow()

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})

app.on('will-quit', () => {
  if (backendProcess) {
    // On Windows, child_process.kill() doesn't kill the process tree.
    // Use taskkill to ensure all child processes are terminated.
    if (process.platform === 'win32') {
      try {
        require('child_process').execSync(`taskkill /pid ${backendProcess.pid} /T /F`, { stdio: 'ignore' })
      } catch (_) {
        backendProcess.kill()
      }
    } else {
      backendProcess.kill()
    }
    backendProcess = null
  }
})
