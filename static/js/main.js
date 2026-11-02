/*=============== SHOW HIDE PASSWORD ===============*/
const bindPasswordToggle = (inputId, toggleId) => {
  const input = document.getElementById(inputId)
  const toggle = document.getElementById(toggleId)
  if (!input || !toggle) return

  const icon = toggle.querySelector('use')

  toggle.addEventListener('click', () => {
    const show = input.type === 'password'
    input.type = show ? 'text' : 'password'
    if (icon) {
      icon.setAttribute('href', `/static/assets/icons.svg#${show ? 'eye' : 'eye-off'}`)
    }
    toggle.setAttribute('aria-label', show ? '隐藏密码' : '显示密码')
  })
}

bindPasswordToggle('password', 'loginPassword')
bindPasswordToggle('passwordCreate', 'loginPasswordCreate')
bindPasswordToggle('passwordForgot', 'forgotPasswordToggle')
bindPasswordToggle('passwordForgotConfirm', 'forgotPasswordConfirmToggle')

/*=============== AUTH ALERTS ===============*/
const loginAlert = document.getElementById('loginAlert')
const registerAlert = document.getElementById('registerAlert')
const forgotAlert = document.getElementById('forgotAlert')

const setAlert = (el, message, type = 'error') => {
  if (!el) return
  if (!message) {
    el.textContent = ''
    el.classList.add('hidden')
    el.classList.remove('auth__alert--error', 'auth__alert--success')
    return
  }
  el.textContent = message
  el.classList.remove('hidden', 'auth__alert--error', 'auth__alert--success')
  el.classList.add(type === 'success' ? 'auth__alert--success' : 'auth__alert--error')
}

const clearFieldInvalid = (form) => {
  form?.querySelectorAll('.field--invalid').forEach((field) => {
    field.classList.remove('field--invalid')
  })
}

const markInvalid = (input) => {
  input?.closest('.field')?.classList.add('field--invalid')
}

const bindClearOnInput = (form, alertEl) => {
  form?.querySelectorAll('input').forEach((input) => {
    input.addEventListener('input', () => {
      input.closest('.field')?.classList.remove('field--invalid')
      if (alertEl && !alertEl.classList.contains('hidden') && alertEl.classList.contains('auth__alert--error')) {
        setAlert(alertEl, '')
      }
    })
  })
}

/*=============== PANEL SWITCH ===============*/
const loginAccessRegister = document.getElementById('loginAccessRegister')
const loginPanel = document.querySelector('[data-panel="login"]')
const registerPanel = document.querySelector('[data-panel="register"]')
const forgotPanel = document.querySelector('[data-panel="forgot"]')

const syncAuthPanels = () => {
  const mode = loginAccessRegister?.dataset.mode || 'login'
  if (loginPanel) loginPanel.inert = mode !== 'login'
  if (registerPanel) registerPanel.inert = mode !== 'register'
  if (forgotPanel) forgotPanel.inert = mode !== 'forgot'
}

const setAuthMode = (mode) => {
  if (!loginAccessRegister) return
  loginAccessRegister.dataset.mode = mode
  syncAuthPanels()
}

const showLoginPanel = () => setAuthMode('login')
const showRegisterPanel = () => setAuthMode('register')
const showForgotPanel = () => setAuthMode('forgot')

syncAuthPanels()

document.getElementById('loginButtonRegister')?.addEventListener('click', () => {
  setAlert(registerAlert, '')
  clearFieldInvalid(document.getElementById('registerForm'))
  showRegisterPanel()
  document.getElementById('names')?.focus()
})

document.getElementById('loginButtonAccess')?.addEventListener('click', () => {
  setAlert(loginAlert, '')
  clearFieldInvalid(document.getElementById('loginForm'))
  showLoginPanel()
  document.getElementById('email')?.focus()
})

document.getElementById('loginButtonForgot')?.addEventListener('click', () => {
  setAlert(forgotAlert, '')
  clearFieldInvalid(document.getElementById('forgotForm'))
  const loginEmail = (document.getElementById('email')?.value || '').trim()
  const forgotEmail = document.getElementById('emailForgot')
  if (forgotEmail && loginEmail) forgotEmail.value = loginEmail
  showForgotPanel()
  ;(forgotEmail?.value ? document.getElementById('passwordForgot') : forgotEmail)?.focus()
})

document.getElementById('forgotButtonAccess')?.addEventListener('click', () => {
  setAlert(loginAlert, '')
  clearFieldInvalid(document.getElementById('loginForm'))
  showLoginPanel()
  document.getElementById('email')?.focus()
})

/*=============== FLASH FROM SERVER ===============*/
;(() => {
  const flashEl = document.getElementById('authFlashData')
  if (!flashEl) return

  let flashes = []
  try {
    flashes = JSON.parse(flashEl.textContent || '[]')
  } catch (_) {
    return
  }

  flashes.forEach(([category, message]) => {
    if (category === 'login_error') {
      showLoginPanel()
      setAlert(loginAlert, message, 'error')
      markInvalid(document.getElementById('email'))
      markInvalid(document.getElementById('password'))
      document.getElementById('password')?.focus()
    } else if (category === 'login_success') {
      showLoginPanel()
      setAlert(loginAlert, message, 'success')
      document.getElementById('email')?.focus()
    } else if (category === 'register_error') {
      showRegisterPanel()
      setAlert(registerAlert, message, 'error')
      document.getElementById('emailCreate')?.focus()
    } else if (category === 'forgot_error') {
      showForgotPanel()
      setAlert(forgotAlert, message, 'error')
      document.getElementById('emailForgot')?.focus()
    }
  })
})()

/*=============== ENTER TO SUBMIT ===============*/
const bindEnterSubmit = (formId) => {
  const form = document.getElementById(formId)
  if (!form) return

  form.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter' || event.isComposing) return
    if (!event.target.matches('input')) return

    event.preventDefault()
    if (typeof form.requestSubmit === 'function') {
      form.requestSubmit()
    } else {
      form.submit()
    }
  })
}

bindEnterSubmit('loginForm')
bindEnterSubmit('registerForm')
bindEnterSubmit('forgotForm')

/*=============== CLIENT VALIDATION ===============*/
function validatePassword(password) {
  return /^(?=.*[0-9])(?=.*[a-z])(?=.*[A-Z])(?=.*[\W_]).{8,}$/.test(password)
}

const loginForm = document.getElementById('loginForm')
if (loginForm) {
  loginForm.addEventListener('submit', (e) => {
    clearFieldInvalid(loginForm)
    const email = document.getElementById('email')
    const password = document.getElementById('password')
    const emailVal = (email?.value || '').trim()
    const passwordVal = password?.value || ''

    if (!emailVal || !passwordVal) {
      e.preventDefault()
      setAlert(loginAlert, '请输入邮箱和密码', 'error')
      if (!emailVal) markInvalid(email)
      if (!passwordVal) markInvalid(password)
      ;(!emailVal ? email : password)?.focus()
      return
    }

    setAlert(loginAlert, '')
  })
  bindClearOnInput(loginForm, loginAlert)
}

const registerForm = document.getElementById('registerForm')
if (registerForm) {
  registerForm.addEventListener('submit', (e) => {
    clearFieldInvalid(registerForm)
    const name = document.getElementById('names')
    const email = document.getElementById('emailCreate')
    const password = document.getElementById('passwordCreate')
    const nameVal = (name?.value || '').trim()
    const emailVal = (email?.value || '').trim()
    const passwordVal = password?.value || ''

    if (!nameVal || !emailVal || !passwordVal) {
      e.preventDefault()
      setAlert(registerAlert, '请完整填写姓名、邮箱和密码', 'error')
      if (!nameVal) markInvalid(name)
      if (!emailVal) markInvalid(email)
      if (!passwordVal) markInvalid(password)
      ;(!nameVal ? name : !emailVal ? email : password)?.focus()
      return
    }

    if (!validatePassword(passwordVal)) {
      e.preventDefault()
      setAlert(
        registerAlert,
        '密码须至少 8 位，且包含大小写字母、数字与特殊符号',
        'error',
      )
      markInvalid(password)
      password?.focus()
      return
    }

    setAlert(registerAlert, '')
  })
  bindClearOnInput(registerForm, registerAlert)
}

const forgotForm = document.getElementById('forgotForm')
if (forgotForm) {
  forgotForm.addEventListener('submit', (e) => {
    clearFieldInvalid(forgotForm)
    const email = document.getElementById('emailForgot')
    const password = document.getElementById('passwordForgot')
    const confirm = document.getElementById('passwordForgotConfirm')
    const emailVal = (email?.value || '').trim()
    const passwordVal = password?.value || ''
    const confirmVal = confirm?.value || ''

    if (!emailVal || !passwordVal || !confirmVal) {
      e.preventDefault()
      setAlert(forgotAlert, '请完整填写邮箱和新密码', 'error')
      if (!emailVal) markInvalid(email)
      if (!passwordVal) markInvalid(password)
      if (!confirmVal) markInvalid(confirm)
      ;(!emailVal ? email : !passwordVal ? password : confirm)?.focus()
      return
    }

    if (passwordVal !== confirmVal) {
      e.preventDefault()
      setAlert(forgotAlert, '两次输入的新密码不一致', 'error')
      markInvalid(confirm)
      confirm?.focus()
      return
    }

    if (!validatePassword(passwordVal)) {
      e.preventDefault()
      setAlert(
        forgotAlert,
        '新密码须至少 8 位，且包含大小写字母、数字与特殊符号',
        'error',
      )
      markInvalid(password)
      password?.focus()
      return
    }

    setAlert(forgotAlert, '')
  })
  bindClearOnInput(forgotForm, forgotAlert)
}

// Password strength is visual feedback only; original validation remains authoritative.
for (const id of ['passwordCreate','passwordForgot']) {
  const input = document.getElementById(id);
  input?.addEventListener('input', () => {
    const value = input.value;
    const strength = [value.length >= 8, /[a-z]/.test(value) && /[A-Z]/.test(value), /[0-9]/.test(value), /[\W_]/.test(value)].filter(Boolean).length;
    input.closest('form').querySelectorAll('.strength-bars b').forEach((el,i) => el.classList.toggle('filled', i < strength));
  });
}
