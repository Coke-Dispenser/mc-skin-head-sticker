<script setup>
import { computed, onMounted, ref } from 'vue'
import { ArrowLeft, Check, Download, GripVertical, Home, ImagePlus, Images, Layers, LogOut, MousePointer2, Plus, RefreshCw, RotateCw, Scan, Shield, Trash2, Upload, UserRound, X, ZoomIn, ZoomOut } from 'lucide-vue-next'
import PhotoCanvas from './PhotoCanvas.vue'

const role = ref('')
const booting = ref(true)
const photos = ref([])
const photo = ref(null)
const selected = ref('')
const active = computed(() => photo.value?.avatars.find(a => a.id === selected.value))
const busy = ref(false)
const message = ref('')
const status = ref('已保存')
const username = ref('')
const password = ref('')
const avatarMode = ref('minecraft')
const avatarName = ref('')
const avatarFile = ref(null)
const avatarInput = ref(null)
const photoInput = ref(null)
const zoom = ref(1)
const confirmation = ref(null)
const savingTitle = computed(() => busy.value ? '正在处理…' : status.value)
const avatarSize = computed(() => active.value ? Math.round(active.value.size * photo.value.width) : 0)
// The API and canvas draw bottom to top; the list shows the top layer first.
const layerList = computed(() => [...(photo.value?.avatars || [])].reverse())
const activeLayerIndex = computed(() => photo.value?.avatars.findIndex(a => a.id === selected.value) ?? -1)
const atTop = computed(() => activeLayerIndex.value === (photo.value?.avatars.length ?? 0) - 1)
const atBottom = computed(() => activeLayerIndex.value === 0)
const draggedLayer = ref('')
const layerDrop = ref(null)

async function api(path, options = {}) {
  const response = await fetch('/api' + path, options)
  if (!response.ok) {
    let detail = '操作失败，请稍后重试'
    try {
      const body = await response.json()
      detail = typeof body.detail === 'string' ? body.detail : '输入内容有误，请检查后重试'
    } catch {}
    if (response.status === 401) { role.value = ''; photo.value = null }
    throw new Error(detail)
  }
  return response.json()
}
function json(method, body) { return { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } }
async function run(action) {
  if (busy.value) return
  busy.value = true
  message.value = ''
  try { await action() } catch (error) { message.value = error.message }
  finally { busy.value = false }
}
async function loadPhotos() { photos.value = await api('/photos') }
async function login() {
  await run(async () => {
    const session = await api('/login', json('POST', { username: username.value, password: password.value }))
    role.value = session.role
    password.value = ''
    await loadPhotos()
  })
}
async function logout() {
  await run(async () => { await api('/logout', { method: 'POST' }); role.value = ''; photo.value = null; photos.value = [] })
}
async function openPhoto(id) {
  await run(async () => {
    photo.value = await api('/photos/' + id)
    selected.value = ''
    zoom.value = 1
    status.value = '已载入最新版本'
    window.scrollTo(0, 0)
  })
}
async function back() {
  await run(async () => { await loadPhotos(); photo.value = null; selected.value = ''; window.scrollTo(0, 0) })
}
async function refresh() {
  await run(async () => {
    if (photo.value) {
      photo.value = await api('/photos/' + photo.value.id)
      if (!photo.value.avatars.some(a => a.id === selected.value)) selected.value = ''
    } else await loadPhotos()
    status.value = '已刷新'
  })
}
async function uploadPhotos(event) {
  const files = [...event.target.files]
  event.target.value = ''
  await run(async () => {
    const failures = []
    for (const file of files) {
      status.value = '正在上传 ' + file.name
      const form = new FormData()
      form.append('file', file)
      try { await api('/photos', { method: 'POST', body: form }) }
      catch (error) { failures.push(file.name + ' · ' + error.message) }
    }
    await loadPhotos()
    status.value = '上传完成'
    if (failures.length) message.value = failures.join('；')
  })
}
async function addAvatar() {
  await run(async () => {
    const form = new FormData()
    form.append('username', avatarName.value.trim())
    form.append('mode', avatarMode.value)
    if (avatarFile.value) form.append('file', avatarFile.value)
    const result = await api('/photos/' + photo.value.id + '/avatars', { method: 'POST', body: form })
    photo.value = result.photo
    selected.value = result.selected
    avatarName.value = ''
    avatarFile.value = null
    if (avatarInput.value) avatarInput.value.value = ''
    status.value = '头像已保存'
  })
}
async function transform(data) {
  await run(async () => {
    try {
      photo.value = await api('/photos/' + photo.value.id + '/avatars/' + data.id, json('PATCH', data))
      status.value = '已保存'
    } catch (error) {
      // Rebuild the canvas from the last acknowledged state after a failed save.
      photo.value = { ...photo.value }
      status.value = '调整未保存'
      throw error
    }
  })
}
function changeSelected(field, value) {
  if (!active.value) return
  transform({ ...active.value, [field]: Number(value) })
}
function askDelete(kind, item) { confirmation.value = { kind, item } }
async function confirmDelete() {
  const { kind, item } = confirmation.value
  confirmation.value = null
  await run(async () => {
    if (kind === 'photo') {
      await api('/photos/' + item.id, { method: 'DELETE' })
      await loadPhotos()
    } else {
      photo.value = await api('/photos/' + photo.value.id + '/avatars/' + item.id + '?version=' + item.version, { method: 'DELETE' })
      selected.value = ''
    }
    status.value = '已删除'
  })
}
async function uniform() {
  await run(async () => {
    photo.value = await api('/photos/' + photo.value.id + '/uniform', json('POST', { version: photo.value.version, size: active.value.size }))
    status.value = '头像大小已统一'
  })
}
async function saveLayerOrder(ids) {
  if (ids.every((id, index) => id === photo.value.avatars[index]?.id)) return
  await run(async () => {
    photo.value = await api('/photos/' + photo.value.id + '/layers', json('PUT', {
      version: photo.value.version, avatar_ids: ids
    }))
    status.value = '图层顺序已保存'
  })
}
function moveLayer(direction) {
  if (busy.value || !active.value) return
  const ids = photo.value.avatars.map(a => a.id)
  const from = activeLayerIndex.value
  const to = { up: from + 1, down: from - 1, top: ids.length - 1, bottom: 0 }[direction]
  if (to < 0 || to >= ids.length || to === from) return
  const [id] = ids.splice(from, 1)
  ids.splice(to, 0, id)
  saveLayerOrder(ids)
}
function startLayerDrag(event, id) {
  if (busy.value) { event.preventDefault(); return }
  draggedLayer.value = id
  event.dataTransfer.effectAllowed = 'move'
  event.dataTransfer.setData('text/plain', id)
  event.dataTransfer.setDragImage(event.currentTarget.closest('.avatar-row'), 24, 24)
}
function overLayer(event, id) {
  if (!draggedLayer.value || busy.value) return
  event.preventDefault()
  event.dataTransfer.dropEffect = 'move'
  const rect = event.currentTarget.getBoundingClientRect()
  layerDrop.value = { id, after: event.clientY >= rect.top + rect.height / 2 }
}
function endLayerDrag() { draggedLayer.value = ''; layerDrop.value = null }
function dropLayer(event, targetId) {
  if (!draggedLayer.value || busy.value) return
  event.preventDefault()
  const sourceId = draggedLayer.value
  const rect = event.currentTarget.getBoundingClientRect()
  const after = event.clientY >= rect.top + rect.height / 2
  endLayerDrag()
  if (sourceId === targetId) return
  const ids = layerList.value.map(a => a.id).filter(id => id !== sourceId)
  const target = ids.indexOf(targetId)
  if (target < 0) return
  ids.splice(target + (after ? 1 : 0), 0, sourceId)
  selected.value = sourceId
  saveLayerOrder(ids.reverse())
}
async function download() {
  await run(async () => {
    const response = await fetch('/api/photos/' + photo.value.id + '/export')
    if (!response.ok) {
      const error = await response.json()
      throw new Error(error.detail || '导出失败，请刷新后重试')
    }
    const blob = await response.blob()
    const href = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = href
    link.download = photo.value.title + '-BCL合照.png'
    link.click()
    setTimeout(() => URL.revokeObjectURL(href), 60000)
    status.value = '已导出 PNG'
  })
}
onMounted(async () => {
  try { role.value = (await api('/session')).role; await loadPhotos() }
  catch (error) { if (role.value) message.value = error.message }
  finally { booting.value = false }
})
</script>

<template>
  <header class="site-header">
    <button class="brand" :disabled="busy" @click="photo && back()"><Home :size="26" /><b>BCL</b><span>方块合照</span></button>
    <div v-if="role" class="header-actions"><span class="role-label"><Shield v-if="role === 'admin'" :size="16" /><UserRound v-else :size="16" />{{ role === 'admin' ? '管理员' : '社团成员' }}</span><button class="icon-button" title="退出登录" aria-label="退出登录" :disabled="busy" @click="logout"><LogOut :size="19" /></button></div>
  </header>
  <div v-if="booting" class="initial-loading">正在打开方块合照…</div>
  <template v-else-if="!role">
    <main class="login-page">
      <section class="login-card">
        <img class="club-logo" src="/favicon.png" alt="BCL" />
        <p class="eyebrow">SEU Minecraft · 六朝松信标社</p>
        <h1>把方块里的自己<br />带进我们的合照</h1>
        <p class="login-intro">选一张合照，贴上你的 Minecraft 头像。<br />一起留下属于社团的回忆。</p>
        <form @submit.prevent="login">
          <label>账号<input v-model="username" required maxlength="80" autocomplete="username" /></label>
          <label>密码<input v-model="password" required type="password" maxlength="256" autocomplete="current-password" /></label>
          <p v-if="message" role="alert" class="error-message">{{ message }}</p>
          <button class="primary full" :disabled="busy">{{ busy ? '正在登录…' : '进入方块合照' }}</button>
        </form>
      </section>
    </main>
  </template>
  <template v-else>
    <div class="page-banner" :class="{ compact: photo }"></div>
    <main class="workspace" :class="{ 'editor-workspace': photo }">
      <p v-if="message" role="alert" class="error-banner">{{ message }}<button aria-label="关闭提示" @click="message = ''"><X :size="18" /></button></p>
      <template v-if="!photo">
        <section class="surface gallery-heading">
          <div><p class="eyebrow">方块合照</p><h1 class="marked-title">我们的合照</h1><p class="muted">{{ photos.length }} 张合照 · 点开照片，贴上你的头像</p></div>
          <div class="button-row">
            <button class="soft-button" :disabled="busy" @click="refresh"><RefreshCw :size="17" />刷新</button>
            <button v-if="role === 'admin'" class="primary" :disabled="busy" @click="photoInput.click()"><Upload :size="18" />上传合照</button>
            <input v-if="role === 'admin'" ref="photoInput" hidden type="file" multiple accept="image/jpeg,image/png,image/webp" @change="uploadPhotos" />
          </div>
        </section>
        <p v-if="busy" class="muted" role="status">{{ savingTitle }}</p>
        <div v-if="photos.length" class="photo-grid">
          <article v-for="item in photos" :key="item.id" class="surface photo-card">
            <button class="photo-open" :disabled="busy" @click="openPhoto(item.id)">
              <img :src="'/api/photos/' + item.id + '/thumbnail?v=' + item.version" alt="" loading="lazy" />
              <span class="photo-card-body"><b>{{ item.title }}</b><span>{{ item.count }} 人已贴头像 · {{ item.width }} × {{ item.height }}</span></span>
            </button>
            <button v-if="role === 'admin'" class="delete-photo icon-button" :disabled="busy" :aria-label="'删除合照 ' + item.title" @click="askDelete('photo', item)"><Trash2 :size="17" /></button>
          </article>
        </div>
        <section v-else class="surface empty-gallery"><Images :size="44" /><h2>下一张合照，从这里开始</h2><p>{{ role === 'admin' ? '上传聚餐、活动或出游照片，邀请大家一起贴头像。支持一次选择多张。' : '管理员上传合照后，就可以在这里一起贴头像。' }}</p><button v-if="role === 'admin'" class="primary" :disabled="busy" @click="photoInput.click()"><Plus :size="18" />上传第一张合照</button></section>
      </template>
      <template v-else>
        <section class="surface editor-heading">
          <div class="editor-title"><button class="icon-button" aria-label="返回合照列表" :disabled="busy" @click="back"><ArrowLeft :size="21" /></button><div><h1>{{ photo.title }}</h1><p class="muted" role="status"><Check :size="14" />{{ savingTitle }}</p></div></div>
          <div class="button-row"><button class="soft-button" :disabled="busy" @click="refresh"><RefreshCw :size="17" />刷新合照</button><button class="primary" :disabled="busy" @click="download"><Download :size="17" />导出照片</button></div>
        </section>
        <div class="editor-grid">
          <section class="surface canvas-card">
            <div class="canvas-toolbar"><span><MousePointer2 :size="16" />拖动头像 · 四角缩放 · 顶部旋转</span><div class="button-row"><button class="icon-button" aria-label="缩小画布" :disabled="busy || zoom <= .5" @click="zoom = Math.max(.5, zoom - .25)"><ZoomOut :size="18" /></button><button class="zoom-reset" :disabled="busy" title="适应画布" @click="zoom = 1">{{ Math.round(zoom * 100) }}%</button><button class="icon-button" aria-label="放大画布" :disabled="busy || zoom >= 3" @click="zoom = Math.min(3, zoom + .25)"><ZoomIn :size="18" /></button></div></div>
            <PhotoCanvas :photo="photo" :selected="selected" :busy="busy" :zoom="zoom" @select="selected = $event" @transform="transform" @error="message = $event" />
            <div class="canvas-caption"><span>{{ photo.width }} × {{ photo.height }} · 原尺寸 PNG 导出</span><span>调整后自动保存，刷新查看他人的修改</span></div>
          </section>
          <aside class="editor-sidebar">
            <section class="surface side-card">
              <h2><ImagePlus :size="19" />贴上我的头像</h2>
              <div class="tabs"><button :class="{ active: avatarMode === 'minecraft' }" :disabled="busy" @click="avatarMode = 'minecraft'">正版 ID</button><button :class="{ active: avatarMode === 'upload' }" :disabled="busy" @click="avatarMode = 'upload'">上传头像</button></div>
              <form @submit.prevent="addAvatar">
                <label>{{ avatarMode === 'minecraft' ? 'Java 版正版 ID' : '你的 ID' }}<input v-model="avatarName" :disabled="busy" required maxlength="40" placeholder="例如 Steve" /></label>
                <label v-if="avatarMode === 'upload'" class="file-picker"><Upload :size="18" />{{ avatarFile?.name || '选择头像图片' }}<input ref="avatarInput" type="file" accept="image/png,image/jpeg,image/webp" :disabled="busy" required @change="avatarFile = $event.target.files[0]" /></label>
                <p class="field-help">{{ avatarMode === 'minecraft' ? '自动获取公开皮肤的正面头像，包含帽子层。' : '支持透明 PNG、JPG 和 WebP，低分辨率头像会按像素放大。' }}</p>
                <button class="primary full" :disabled="busy || !avatarName.trim() || (avatarMode === 'upload' && !avatarFile)"><Plus :size="17" />添加到合照</button>
              </form>
            </section>
            <section v-if="active" class="surface side-card selection-card">
              <h2><img :src="'/api/avatars/' + active.id + '/image'" alt="" />{{ active.username }}</h2>
              <div class="layer-heading"><Layers :size="16" /><span>图层顺序</span><span class="layer-position">{{ atTop ? '最上层' : atBottom ? '最下层' : '第 ' + (photo.avatars.length - activeLayerIndex) + ' 层' }}</span></div>
              <div class="layer-actions" role="group" aria-label="调整头像图层顺序">
                <button class="soft-button" :disabled="busy || atTop" @click="moveLayer('up')">上移一层</button>
                <button class="soft-button" :disabled="busy || atBottom" @click="moveLayer('down')">下移一层</button>
                <button class="soft-button" :disabled="busy || atTop" @click="moveLayer('top')">置顶</button>
                <button class="soft-button" :disabled="busy || atBottom" @click="moveLayer('bottom')">置底</button>
              </div>
              <label class="range-label">头像宽度 <span>{{ avatarSize }} px</span><input type="range" aria-label="头像大小" :value="active.size" min=".005" max=".5" step=".005" :disabled="busy" @change="changeSelected('size', $event.target.value)" /></label>
              <label class="range-label">旋转 <span>{{ Math.round(active.rotation) }}°</span><input type="range" aria-label="头像旋转" :value="active.rotation" min="0" max="359" step="1" :disabled="busy" @change="changeSelected('rotation', $event.target.value)" /></label>
              <button class="soft-button full" :disabled="busy" @click="uniform"><Scan :size="17" />全部统一为这个大小</button>
              <div class="selection-actions"><button class="text-button" :disabled="busy" @click="changeSelected('rotation', 0)"><RotateCw :size="15" />重置旋转</button><button class="danger text-button" :disabled="busy" @click="askDelete('avatar', active)"><Trash2 :size="15" />移除头像</button></div>
            </section>
            <section class="surface side-card">
              <h2><UserRound :size="18" />已贴头像 <span class="count">{{ photo.avatars.length }}</span></h2>
              <p v-if="!photo.avatars.length" class="field-help">还没有头像，来贴第一个吧。</p>
              <template v-else>
                <p class="field-help layer-guide">上面的头像盖住下面的头像。被遮住时，可从这里选中。</p>
                <div class="avatar-list" role="list" aria-label="头像图层，从上到下">
                  <div v-for="item in layerList" :key="item.id" class="avatar-row" role="listitem"
                    :class="{ active: selected === item.id, dragging: draggedLayer === item.id, 'drop-before': layerDrop?.id === item.id && !layerDrop.after, 'drop-after': layerDrop?.id === item.id && layerDrop.after }"
                    @dragover="overLayer($event, item.id)" @drop="dropLayer($event, item.id)" @dragleave="layerDrop?.id === item.id && (layerDrop = null)">
                    <button class="layer-handle" :draggable="!busy" :disabled="busy" :aria-label="'拖动调整 ' + item.username + ' 的图层顺序'" title="拖动调整图层顺序"
                      @dragstart="startLayerDrag($event, item.id)" @dragend="endLayerDrag" @click="selected = item.id"><GripVertical :size="18" /></button>
                    <button class="avatar-select" :disabled="busy" :aria-pressed="selected === item.id" @click="selected = item.id"><img :src="'/api/avatars/' + item.id + '/image'" alt="" draggable="false" /><span>{{ item.username }}</span><MousePointer2 v-if="selected === item.id" :size="15" /></button>
                  </div>
                </div>
                <p class="field-help">选中头像后可调整图层顺序，电脑端也可拖动左侧手柄排序。</p>
              </template>
              <p class="field-help">新头像默认在最上层，沿用首个头像的尺寸。</p>
            </section>
          </aside>
        </div>
      </template>
    </main>
    <footer>BCL · 东南大学 Minecraft 六朝松信标社</footer>
  </template>
  <div v-if="confirmation" class="modal-backdrop" @click.self="confirmation = null">
    <section role="dialog" aria-modal="true" aria-labelledby="confirm-title" class="surface confirm-dialog">
      <h2 id="confirm-title">{{ confirmation.kind === 'photo' ? '删除这张合照？' : '移除这个头像？' }}</h2>
      <p>{{ confirmation.kind === 'photo' ? '合照和其中所有头像的编辑记录都会被删除。' : '移除后，可以重新添加这个 ID 的头像。' }}</p>
      <div class="button-row"><button class="soft-button" @click="confirmation = null">取消</button><button class="danger-button" @click="confirmDelete">确认删除</button></div>
    </section>
  </div>
</template>
