<script setup>
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import Konva from 'konva'

const props = defineProps({
  photo: { type: Object, required: true },
  selected: { type: String, default: '' },
  busy: Boolean,
  zoom: { type: Number, default: 1 }
})
const emit = defineEmits(['select', 'transform', 'error'])
const viewport = ref(null)
const mount = ref(null)
const hovering = ref(null)
const loaded = ref(false)
let stage, background, overlays, transformer, observer
let sequence = 0
let dragging = false
const nodes = new Map()

function imageFrom(url) {
  return new Promise((resolve, reject) => {
    const image = new Image()
    image.onload = () => resolve(image)
    image.onerror = () => reject(new Error('图片加载失败，请刷新合照'))
    image.src = url
  })
}
function dimensions() {
  const available = viewport.value.clientWidth
  const maxHeight = window.innerWidth < 768 ? 380 : Math.max(350, window.innerHeight - 270)
  const width = Math.min(available, maxHeight * props.photo.width / props.photo.height) * props.zoom
  return { width, height: width * props.photo.height / props.photo.width }
}
function selectNode() {
  transformer.nodes(nodes.has(props.selected) ? [nodes.get(props.selected)] : [])
  overlays.batchDraw()
}
function constrain(position) {
  return { x: Math.max(0, Math.min(stage.width(), position.x)), y: Math.max(0, Math.min(stage.height(), position.y)) }
}
function commit(node, item) {
  dragging = false
  if (props.busy) return
  const size = Math.max(.005, Math.min(1, node.width() * node.scaleX() / stage.width()))
  emit('transform', {
    id: item.id, version: item.version,
    x: Math.max(0, Math.min(1, node.x() / stage.width())),
    y: Math.max(0, Math.min(1, node.y() / stage.height())),
    size, rotation: ((node.rotation() % 360) + 360) % 360
  })
}
async function draw() {
  if (!stage || dragging) return
  const generation = ++sequence
  loaded.value = false
  hovering.value = null
  try {
    const [photo, ...images] = await Promise.all([
      imageFrom('/api/photos/' + props.photo.id + '/image?preview=true'),
      ...props.photo.avatars.map(a => imageFrom('/api/avatars/' + a.id + '/image'))
    ])
    if (generation !== sequence) return
    const { width, height } = dimensions()
    stage.size({ width, height })
    transformer.nodes([])
    background.destroyChildren()
    for (const node of nodes.values()) node.destroy()
    nodes.clear()
    background.add(new Konva.Image({ image: photo, width, height, listening: false }))
    props.photo.avatars.forEach((item, index) => {
      const side = item.size * width
      const node = new Konva.Image({
        image: images[index], width: side, height: side,
        x: item.x * width, y: item.y * height, rotation: item.rotation,
        offsetX: side / 2, offsetY: side / 2,
        draggable: !props.busy, dragBoundFunc: constrain
      })
      node.on('mousedown touchstart', () => {
        if (props.busy) return
        emit('select', item.id)
        transformer.nodes([node])
      })
      node.on('dragstart transformstart', () => { dragging = true; hovering.value = null })
      node.on('dragend transformend', () => commit(node, item))
      node.on('mousemove', () => {
        const pointer = stage.getPointerPosition()
        hovering.value = { text: item.username, x: Math.max(4, Math.min(width - 140, pointer.x + 10)), y: Math.max(4, pointer.y - 36) }
        stage.container().style.cursor = 'move'
      })
      node.on('mouseout', () => { hovering.value = null; stage.container().style.cursor = 'default' })
      overlays.add(node)
      nodes.set(item.id, node)
    })
    transformer.moveToTop()
    selectNode()
    background.batchDraw()
    overlays.batchDraw()
    loaded.value = true
  } catch (error) {
    if (generation === sequence) emit('error', error.message)
  }
}
onMounted(async () => {
  await nextTick()
  stage = new Konva.Stage({ container: mount.value, width: 1, height: 1 })
  background = new Konva.Layer()
  overlays = new Konva.Layer({ imageSmoothingEnabled: false })
  transformer = new Konva.Transformer({
    keepRatio: true, flipEnabled: false, centeredScaling: true,
    enabledAnchors: ['top-left', 'top-right', 'bottom-left', 'bottom-right'],
    anchorSize: 12, anchorCornerRadius: 4, anchorFill: '#ffffff',
    borderStroke: '#458ac7', anchorStroke: '#458ac7', rotateAnchorOffset: 24,
    boundBoxFunc: (oldBox, newBox) => Math.abs(newBox.width) < 8 || Math.abs(newBox.width) > stage.width() ? oldBox : newBox
  })
  overlays.add(transformer)
  stage.add(background, overlays)
  stage.on('click tap', e => { if (e.target === stage && !props.busy) emit('select', '') })
  observer = new ResizeObserver(draw)
  observer.observe(viewport.value)
  draw()
})
watch(() => props.photo, draw)
watch(() => props.zoom, draw)
watch(() => props.selected, () => { if (stage) selectNode() })
watch(() => props.busy, value => {
  for (const node of nodes.values()) node.draggable(!value)
  if (transformer) transformer.listening(!value)
})
onBeforeUnmount(() => { sequence++; observer?.disconnect(); stage?.destroy() })
</script>

<template>
  <div ref="viewport" class="canvas-scroll" :class="{ saving: busy }">
    <div class="canvas-position">
      <div ref="mount" class="canvas-mount"></div>
      <div v-if="hovering" class="avatar-tooltip" :style="{ left: hovering.x + 'px', top: hovering.y + 'px' }">{{ hovering.text }}</div>
    </div>
    <p v-if="!loaded" class="canvas-loading">正在加载合照…</p>
  </div>
</template>
