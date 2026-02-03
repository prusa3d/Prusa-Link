var __defProp = Object.defineProperty;
var __defNormalProp = (obj, key, value) => key in obj ? __defProp(obj, key, { enumerable: true, configurable: true, writable: true, value }) : obj[key] = value;
var __publicField = (obj, key, value) => {
  __defNormalProp(obj, typeof key !== "symbol" ? key + "" : key, value);
  return value;
};
(function polyfill() {
  const relList = document.createElement("link").relList;
  if (relList && relList.supports && relList.supports("modulepreload")) {
    return;
  }
  for (const link of document.querySelectorAll('link[rel="modulepreload"]')) {
    processPreload(link);
  }
  new MutationObserver((mutations) => {
    for (const mutation of mutations) {
      if (mutation.type !== "childList") {
        continue;
      }
      for (const node of mutation.addedNodes) {
        if (node.tagName === "LINK" && node.rel === "modulepreload")
          processPreload(node);
      }
    }
  }).observe(document, { childList: true, subtree: true });
  function getFetchOpts(link) {
    const fetchOpts = {};
    if (link.integrity)
      fetchOpts.integrity = link.integrity;
    if (link.referrerPolicy)
      fetchOpts.referrerPolicy = link.referrerPolicy;
    if (link.crossOrigin === "use-credentials")
      fetchOpts.credentials = "include";
    else if (link.crossOrigin === "anonymous")
      fetchOpts.credentials = "omit";
    else
      fetchOpts.credentials = "same-origin";
    return fetchOpts;
  }
  function processPreload(link) {
    if (link.ep)
      return;
    link.ep = true;
    const fetchOpts = getFetchOpts(link);
    fetch(link.href, fetchOpts);
  }
})();
function noop() {
}
const identity = (x) => x;
function run(fn) {
  return fn();
}
function blank_object() {
  return /* @__PURE__ */ Object.create(null);
}
function run_all(fns) {
  fns.forEach(run);
}
function is_function(thing) {
  return typeof thing === "function";
}
function safe_not_equal(a, b) {
  return a != a ? b == b : a !== b || a && typeof a === "object" || typeof a === "function";
}
let src_url_equal_anchor;
function src_url_equal(element_src, url) {
  if (element_src === url)
    return true;
  if (!src_url_equal_anchor) {
    src_url_equal_anchor = document.createElement("a");
  }
  src_url_equal_anchor.href = url;
  return element_src === src_url_equal_anchor.href;
}
function is_empty(obj) {
  return Object.keys(obj).length === 0;
}
const is_client = typeof window !== "undefined";
let now = is_client ? () => window.performance.now() : () => Date.now();
let raf = is_client ? (cb) => requestAnimationFrame(cb) : noop;
const tasks = /* @__PURE__ */ new Set();
function run_tasks(now2) {
  tasks.forEach((task) => {
    if (!task.c(now2)) {
      tasks.delete(task);
      task.f();
    }
  });
  if (tasks.size !== 0)
    raf(run_tasks);
}
function loop(callback) {
  let task;
  if (tasks.size === 0)
    raf(run_tasks);
  return {
    promise: new Promise((fulfill) => {
      tasks.add(task = { c: callback, f: fulfill });
    }),
    abort() {
      tasks.delete(task);
    }
  };
}
function append(target, node) {
  target.appendChild(node);
}
function get_root_for_style(node) {
  if (!node)
    return document;
  const root = node.getRootNode ? node.getRootNode() : node.ownerDocument;
  if (root && /** @type {ShadowRoot} */
  root.host) {
    return (
      /** @type {ShadowRoot} */
      root
    );
  }
  return node.ownerDocument;
}
function append_empty_stylesheet(node) {
  const style_element = element("style");
  style_element.textContent = "/* empty */";
  append_stylesheet(get_root_for_style(node), style_element);
  return style_element.sheet;
}
function append_stylesheet(node, style) {
  append(
    /** @type {Document} */
    node.head || node,
    style
  );
  return style.sheet;
}
function insert(target, node, anchor) {
  target.insertBefore(node, anchor || null);
}
function detach(node) {
  if (node.parentNode) {
    node.parentNode.removeChild(node);
  }
}
function destroy_each(iterations, detaching) {
  for (let i = 0; i < iterations.length; i += 1) {
    if (iterations[i])
      iterations[i].d(detaching);
  }
}
function element(name) {
  return document.createElement(name);
}
function text(data) {
  return document.createTextNode(data);
}
function space() {
  return text(" ");
}
function empty() {
  return text("");
}
function listen(node, event, handler, options) {
  node.addEventListener(event, handler, options);
  return () => node.removeEventListener(event, handler, options);
}
function prevent_default(fn) {
  return function(event) {
    event.preventDefault();
    return fn.call(this, event);
  };
}
function stop_propagation(fn) {
  return function(event) {
    event.stopPropagation();
    return fn.call(this, event);
  };
}
function attr(node, attribute, value) {
  if (value == null)
    node.removeAttribute(attribute);
  else if (node.getAttribute(attribute) !== value)
    node.setAttribute(attribute, value);
}
function children(element2) {
  return Array.from(element2.childNodes);
}
function set_data(text2, data) {
  data = "" + data;
  if (text2.data === data)
    return;
  text2.data = /** @type {string} */
  data;
}
function set_style(node, key, value, important) {
  if (value == null) {
    node.style.removeProperty(key);
  } else {
    node.style.setProperty(key, value, important ? "important" : "");
  }
}
function custom_event(type, detail, { bubbles = false, cancelable = false } = {}) {
  return new CustomEvent(type, { detail, bubbles, cancelable });
}
const managed_styles = /* @__PURE__ */ new Map();
let active = 0;
function hash(str) {
  let hash2 = 5381;
  let i = str.length;
  while (i--)
    hash2 = (hash2 << 5) - hash2 ^ str.charCodeAt(i);
  return hash2 >>> 0;
}
function create_style_information(doc, node) {
  const info = { stylesheet: append_empty_stylesheet(node), rules: {} };
  managed_styles.set(doc, info);
  return info;
}
function create_rule(node, a, b, duration, delay, ease, fn, uid = 0) {
  const step = 16.666 / duration;
  let keyframes = "{\n";
  for (let p = 0; p <= 1; p += step) {
    const t = a + (b - a) * ease(p);
    keyframes += p * 100 + `%{${fn(t, 1 - t)}}
`;
  }
  const rule = keyframes + `100% {${fn(b, 1 - b)}}
}`;
  const name = `__svelte_${hash(rule)}_${uid}`;
  const doc = get_root_for_style(node);
  const { stylesheet, rules } = managed_styles.get(doc) || create_style_information(doc, node);
  if (!rules[name]) {
    rules[name] = true;
    stylesheet.insertRule(`@keyframes ${name} ${rule}`, stylesheet.cssRules.length);
  }
  const animation = node.style.animation || "";
  node.style.animation = `${animation ? `${animation}, ` : ""}${name} ${duration}ms linear ${delay}ms 1 both`;
  active += 1;
  return name;
}
function delete_rule(node, name) {
  const previous = (node.style.animation || "").split(", ");
  const next = previous.filter(
    name ? (anim) => anim.indexOf(name) < 0 : (anim) => anim.indexOf("__svelte") === -1
    // remove all Svelte animations
  );
  const deleted = previous.length - next.length;
  if (deleted) {
    node.style.animation = next.join(", ");
    active -= deleted;
    if (!active)
      clear_rules();
  }
}
function clear_rules() {
  raf(() => {
    if (active)
      return;
    managed_styles.forEach((info) => {
      const { ownerNode } = info.stylesheet;
      if (ownerNode)
        detach(ownerNode);
    });
    managed_styles.clear();
  });
}
let current_component;
function set_current_component(component) {
  current_component = component;
}
function get_current_component() {
  if (!current_component)
    throw new Error("Function called outside component initialization");
  return current_component;
}
function onMount(fn) {
  get_current_component().$$.on_mount.push(fn);
}
function bubble(component, event) {
  const callbacks = component.$$.callbacks[event.type];
  if (callbacks) {
    callbacks.slice().forEach((fn) => fn.call(this, event));
  }
}
const dirty_components = [];
const binding_callbacks = [];
let render_callbacks = [];
const flush_callbacks = [];
const resolved_promise = /* @__PURE__ */ Promise.resolve();
let update_scheduled = false;
function schedule_update() {
  if (!update_scheduled) {
    update_scheduled = true;
    resolved_promise.then(flush);
  }
}
function add_render_callback(fn) {
  render_callbacks.push(fn);
}
function add_flush_callback(fn) {
  flush_callbacks.push(fn);
}
const seen_callbacks = /* @__PURE__ */ new Set();
let flushidx = 0;
function flush() {
  if (flushidx !== 0) {
    return;
  }
  const saved_component = current_component;
  do {
    try {
      while (flushidx < dirty_components.length) {
        const component = dirty_components[flushidx];
        flushidx++;
        set_current_component(component);
        update(component.$$);
      }
    } catch (e) {
      dirty_components.length = 0;
      flushidx = 0;
      throw e;
    }
    set_current_component(null);
    dirty_components.length = 0;
    flushidx = 0;
    while (binding_callbacks.length)
      binding_callbacks.pop()();
    for (let i = 0; i < render_callbacks.length; i += 1) {
      const callback = render_callbacks[i];
      if (!seen_callbacks.has(callback)) {
        seen_callbacks.add(callback);
        callback();
      }
    }
    render_callbacks.length = 0;
  } while (dirty_components.length);
  while (flush_callbacks.length) {
    flush_callbacks.pop()();
  }
  update_scheduled = false;
  seen_callbacks.clear();
  set_current_component(saved_component);
}
function update($$) {
  if ($$.fragment !== null) {
    $$.update();
    run_all($$.before_update);
    const dirty = $$.dirty;
    $$.dirty = [-1];
    $$.fragment && $$.fragment.p($$.ctx, dirty);
    $$.after_update.forEach(add_render_callback);
  }
}
function flush_render_callbacks(fns) {
  const filtered = [];
  const targets = [];
  render_callbacks.forEach((c) => fns.indexOf(c) === -1 ? filtered.push(c) : targets.push(c));
  targets.forEach((c) => c());
  render_callbacks = filtered;
}
let promise;
function wait() {
  if (!promise) {
    promise = Promise.resolve();
    promise.then(() => {
      promise = null;
    });
  }
  return promise;
}
function dispatch(node, direction, kind) {
  node.dispatchEvent(custom_event(`${direction ? "intro" : "outro"}${kind}`));
}
const outroing = /* @__PURE__ */ new Set();
let outros;
function group_outros() {
  outros = {
    r: 0,
    c: [],
    p: outros
    // parent group
  };
}
function check_outros() {
  if (!outros.r) {
    run_all(outros.c);
  }
  outros = outros.p;
}
function transition_in(block, local) {
  if (block && block.i) {
    outroing.delete(block);
    block.i(local);
  }
}
function transition_out(block, local, detach2, callback) {
  if (block && block.o) {
    if (outroing.has(block))
      return;
    outroing.add(block);
    outros.c.push(() => {
      outroing.delete(block);
      if (callback) {
        if (detach2)
          block.d(1);
        callback();
      }
    });
    block.o(local);
  } else if (callback) {
    callback();
  }
}
const null_transition = { duration: 0 };
function create_bidirectional_transition(node, fn, params, intro) {
  const options = { direction: "both" };
  let config = fn(node, params, options);
  let t = intro ? 0 : 1;
  let running_program = null;
  let pending_program = null;
  let animation_name = null;
  let original_inert_value;
  function clear_animation() {
    if (animation_name)
      delete_rule(node, animation_name);
  }
  function init2(program, duration) {
    const d = (
      /** @type {Program['d']} */
      program.b - t
    );
    duration *= Math.abs(d);
    return {
      a: t,
      b: program.b,
      d,
      duration,
      start: program.start,
      end: program.start + duration,
      group: program.group
    };
  }
  function go(b) {
    const {
      delay = 0,
      duration = 300,
      easing = identity,
      tick = noop,
      css
    } = config || null_transition;
    const program = {
      start: now() + delay,
      b
    };
    if (!b) {
      program.group = outros;
      outros.r += 1;
    }
    if ("inert" in node) {
      if (b) {
        if (original_inert_value !== void 0) {
          node.inert = original_inert_value;
        }
      } else {
        original_inert_value = /** @type {HTMLElement} */
        node.inert;
        node.inert = true;
      }
    }
    if (running_program || pending_program) {
      pending_program = program;
    } else {
      if (css) {
        clear_animation();
        animation_name = create_rule(node, t, b, duration, delay, easing, css);
      }
      if (b)
        tick(0, 1);
      running_program = init2(program, duration);
      add_render_callback(() => dispatch(node, b, "start"));
      loop((now2) => {
        if (pending_program && now2 > pending_program.start) {
          running_program = init2(pending_program, duration);
          pending_program = null;
          dispatch(node, running_program.b, "start");
          if (css) {
            clear_animation();
            animation_name = create_rule(
              node,
              t,
              running_program.b,
              running_program.duration,
              0,
              easing,
              config.css
            );
          }
        }
        if (running_program) {
          if (now2 >= running_program.end) {
            tick(t = running_program.b, 1 - t);
            dispatch(node, running_program.b, "end");
            if (!pending_program) {
              if (running_program.b) {
                clear_animation();
              } else {
                if (!--running_program.group.r)
                  run_all(running_program.group.c);
              }
            }
            running_program = null;
          } else if (now2 >= running_program.start) {
            const p = now2 - running_program.start;
            t = running_program.a + running_program.d * easing(p / running_program.duration);
            tick(t, 1 - t);
          }
        }
        return !!(running_program || pending_program);
      });
    }
  }
  return {
    run(b) {
      if (is_function(config)) {
        wait().then(() => {
          const opts = { direction: b ? "in" : "out" };
          config = config(opts);
          go(b);
        });
      } else {
        go(b);
      }
    },
    end() {
      clear_animation();
      running_program = pending_program = null;
    }
  };
}
function ensure_array_like(array_like_or_iterator) {
  return (array_like_or_iterator == null ? void 0 : array_like_or_iterator.length) !== void 0 ? array_like_or_iterator : Array.from(array_like_or_iterator);
}
function outro_and_destroy_block(block, lookup) {
  transition_out(block, 1, 1, () => {
    lookup.delete(block.key);
  });
}
function update_keyed_each(old_blocks, dirty, get_key, dynamic, ctx, list, lookup, node, destroy, create_each_block2, next, get_context) {
  let o = old_blocks.length;
  let n = list.length;
  let i = o;
  const old_indexes = {};
  while (i--)
    old_indexes[old_blocks[i].key] = i;
  const new_blocks = [];
  const new_lookup = /* @__PURE__ */ new Map();
  const deltas = /* @__PURE__ */ new Map();
  const updates = [];
  i = n;
  while (i--) {
    const child_ctx = get_context(ctx, list, i);
    const key = get_key(child_ctx);
    let block = lookup.get(key);
    if (!block) {
      block = create_each_block2(key, child_ctx);
      block.c();
    } else if (dynamic) {
      updates.push(() => block.p(child_ctx, dirty));
    }
    new_lookup.set(key, new_blocks[i] = block);
    if (key in old_indexes)
      deltas.set(key, Math.abs(i - old_indexes[key]));
  }
  const will_move = /* @__PURE__ */ new Set();
  const did_move = /* @__PURE__ */ new Set();
  function insert2(block) {
    transition_in(block, 1);
    block.m(node, next);
    lookup.set(block.key, block);
    next = block.first;
    n--;
  }
  while (o && n) {
    const new_block = new_blocks[n - 1];
    const old_block = old_blocks[o - 1];
    const new_key = new_block.key;
    const old_key = old_block.key;
    if (new_block === old_block) {
      next = new_block.first;
      o--;
      n--;
    } else if (!new_lookup.has(old_key)) {
      destroy(old_block, lookup);
      o--;
    } else if (!lookup.has(new_key) || will_move.has(new_key)) {
      insert2(new_block);
    } else if (did_move.has(old_key)) {
      o--;
    } else if (deltas.get(new_key) > deltas.get(old_key)) {
      did_move.add(new_key);
      insert2(new_block);
    } else {
      will_move.add(old_key);
      o--;
    }
  }
  while (o--) {
    const old_block = old_blocks[o];
    if (!new_lookup.has(old_block.key))
      destroy(old_block, lookup);
  }
  while (n)
    insert2(new_blocks[n - 1]);
  run_all(updates);
  return new_blocks;
}
function bind(component, name, callback) {
  const index = component.$$.props[name];
  if (index !== void 0) {
    component.$$.bound[index] = callback;
    callback(component.$$.ctx[index]);
  }
}
function create_component(block) {
  block && block.c();
}
function mount_component(component, target, anchor) {
  const { fragment, after_update } = component.$$;
  fragment && fragment.m(target, anchor);
  add_render_callback(() => {
    const new_on_destroy = component.$$.on_mount.map(run).filter(is_function);
    if (component.$$.on_destroy) {
      component.$$.on_destroy.push(...new_on_destroy);
    } else {
      run_all(new_on_destroy);
    }
    component.$$.on_mount = [];
  });
  after_update.forEach(add_render_callback);
}
function destroy_component(component, detaching) {
  const $$ = component.$$;
  if ($$.fragment !== null) {
    flush_render_callbacks($$.after_update);
    run_all($$.on_destroy);
    $$.fragment && $$.fragment.d(detaching);
    $$.on_destroy = $$.fragment = null;
    $$.ctx = [];
  }
}
function make_dirty(component, i) {
  if (component.$$.dirty[0] === -1) {
    dirty_components.push(component);
    schedule_update();
    component.$$.dirty.fill(0);
  }
  component.$$.dirty[i / 31 | 0] |= 1 << i % 31;
}
function init(component, options, instance2, create_fragment2, not_equal, props, append_styles = null, dirty = [-1]) {
  const parent_component = current_component;
  set_current_component(component);
  const $$ = component.$$ = {
    fragment: null,
    ctx: [],
    // state
    props,
    update: noop,
    not_equal,
    bound: blank_object(),
    // lifecycle
    on_mount: [],
    on_destroy: [],
    on_disconnect: [],
    before_update: [],
    after_update: [],
    context: new Map(options.context || (parent_component ? parent_component.$$.context : [])),
    // everything else
    callbacks: blank_object(),
    dirty,
    skip_bound: false,
    root: options.target || parent_component.$$.root
  };
  append_styles && append_styles($$.root);
  let ready = false;
  $$.ctx = instance2 ? instance2(component, options.props || {}, (i, ret, ...rest) => {
    const value = rest.length ? rest[0] : ret;
    if ($$.ctx && not_equal($$.ctx[i], $$.ctx[i] = value)) {
      if (!$$.skip_bound && $$.bound[i])
        $$.bound[i](value);
      if (ready)
        make_dirty(component, i);
    }
    return ret;
  }) : [];
  $$.update();
  ready = true;
  run_all($$.before_update);
  $$.fragment = create_fragment2 ? create_fragment2($$.ctx) : false;
  if (options.target) {
    if (options.hydrate) {
      const nodes = children(options.target);
      $$.fragment && $$.fragment.l(nodes);
      nodes.forEach(detach);
    } else {
      $$.fragment && $$.fragment.c();
    }
    if (options.intro)
      transition_in(component.$$.fragment);
    mount_component(component, options.target, options.anchor);
    flush();
  }
  set_current_component(parent_component);
}
class SvelteComponent {
  constructor() {
    /**
     * ### PRIVATE API
     *
     * Do not use, may change at any time
     *
     * @type {any}
     */
    __publicField(this, "$$");
    /**
     * ### PRIVATE API
     *
     * Do not use, may change at any time
     *
     * @type {any}
     */
    __publicField(this, "$$set");
  }
  /** @returns {void} */
  $destroy() {
    destroy_component(this, 1);
    this.$destroy = noop;
  }
  /**
   * @template {Extract<keyof Events, string>} K
   * @param {K} type
   * @param {((e: Events[K]) => void) | null | undefined} callback
   * @returns {() => void}
   */
  $on(type, callback) {
    if (!is_function(callback)) {
      return noop;
    }
    const callbacks = this.$$.callbacks[type] || (this.$$.callbacks[type] = []);
    callbacks.push(callback);
    return () => {
      const index = callbacks.indexOf(callback);
      if (index !== -1)
        callbacks.splice(index, 1);
    };
  }
  /**
   * @param {Partial<Props>} props
   * @returns {void}
   */
  $set(props) {
    if (this.$$set && !is_empty(props)) {
      this.$$.skip_bound = true;
      this.$$set(props);
      this.$$.skip_bound = false;
    }
  }
}
const PUBLIC_VERSION = "4";
if (typeof window !== "undefined")
  (window.__svelte || (window.__svelte = { v: /* @__PURE__ */ new Set() })).v.add(PUBLIC_VERSION);
function cubicOut(t) {
  const f = t - 1;
  return f * f * f + 1;
}
function fade(node, { delay = 0, duration = 400, easing = identity } = {}) {
  const o = +getComputedStyle(node).opacity;
  return {
    delay,
    duration,
    easing,
    css: (t) => `opacity: ${t * o}`
  };
}
function slide(node, { delay = 0, duration = 400, easing = cubicOut, axis = "y" } = {}) {
  const style = getComputedStyle(node);
  const opacity = +style.opacity;
  const primary_property = axis === "y" ? "height" : "width";
  const primary_property_value = parseFloat(style[primary_property]);
  const secondary_properties = axis === "y" ? ["top", "bottom"] : ["left", "right"];
  const capitalized_secondary_properties = secondary_properties.map(
    (e) => `${e[0].toUpperCase()}${e.slice(1)}`
  );
  const padding_start_value = parseFloat(style[`padding${capitalized_secondary_properties[0]}`]);
  const padding_end_value = parseFloat(style[`padding${capitalized_secondary_properties[1]}`]);
  const margin_start_value = parseFloat(style[`margin${capitalized_secondary_properties[0]}`]);
  const margin_end_value = parseFloat(style[`margin${capitalized_secondary_properties[1]}`]);
  const border_width_start_value = parseFloat(
    style[`border${capitalized_secondary_properties[0]}Width`]
  );
  const border_width_end_value = parseFloat(
    style[`border${capitalized_secondary_properties[1]}Width`]
  );
  return {
    delay,
    duration,
    easing,
    css: (t) => `overflow: hidden;opacity: ${Math.min(t * 20, 1) * opacity};${primary_property}: ${t * primary_property_value}px;padding-${secondary_properties[0]}: ${t * padding_start_value}px;padding-${secondary_properties[1]}: ${t * padding_end_value}px;margin-${secondary_properties[0]}: ${t * margin_start_value}px;margin-${secondary_properties[1]}: ${t * margin_end_value}px;border-${secondary_properties[0]}-width: ${t * border_width_start_value}px;border-${secondary_properties[1]}-width: ${t * border_width_end_value}px;`
  };
}
const PROBE_TIMEOUT$1 = 2e3;
let requestUrl = window.location.href.split("/").slice(0, -1).join("/");
function handleFormData(e) {
  const ACTION_URL = e.target.action;
  const formData = new FormData(e.target);
  const data = new URLSearchParams();
  for (let field of formData) {
    const [key, value] = field;
    data.append(key, value);
  }
  if (e.target.method.toLowerCase() == "get") {
    fetcher(`${ACTION_URL}?${data}`);
  } else {
    fetcher(ACTION_URL, {
      method: "POST",
      body: data
    });
  }
}
function changeHost(probeUrl) {
  window.location.href = probeUrl + "/wifi";
}
function turnOffHotspot() {
  fetcher(requestUrl + "/wifi/api/hotspot_not_needed", { method: "POST" });
}
const instanceFingerprint = document.getElementById("instance-fingerprint").value;
function addFingerprint(options) {
  const update2 = { ...options };
  update2.headers = {
    ...update2.headers,
    "X-Instance-Fingerprint": instanceFingerprint
  };
  return update2;
}
async function probe(url) {
  try {
    const response = await fetcher(url + "/wifi/api/probe", { method: "HEAD", signal: AbortSignal.timeout(PROBE_TIMEOUT$1) });
    return response.status == 200;
  } catch {
    return false;
  }
}
function fetcher(url, options) {
  return fetch(url, addFingerprint(options));
}
const states = {
  disabled: 0,
  hotspotFlowStart: 1,
  disconnectedOnStart: 2,
  channelSwitch: 4,
  channelSwitchFailed: 5,
  ipsReported: 6,
  hotspotHandoff: 7,
  redirectImminent: 8,
  // Non hotspot states
  hostUnreachable: 100
};
const UPDATE_INTERVAL = 250;
const PROBE_TIMEOUT = 7e3;
const CHANNEL_SWITCH_START_TIMEOUT = 1e3;
const CHANNEL_SWITCH_TIMEOUT = 15e3;
const HOTSPOT_HANDOFF_TIMEOUT = 4e3;
function instance$4($$self, $$props, $$invalidate) {
  const ipAvailableSkipStates = [
    states.hotspotFlowStart,
    states.disconnectedOnStart,
    states.channelSwitch,
    states.channelSwitchFailed
  ];
  const backToStartStates = [
    states.disconnectedOnStart,
    states.channelSwitch,
    states.channelSwitchFailed,
    states.ipsReported,
    states.hotspotHandoff
  ];
  const ipReachableSkipStates = [
    states.hotspotFlowStart,
    states.disconnectedOnStart,
    states.channelSwitch,
    states.channelSwitchFailed,
    states.ipsReported,
    states.hotspotHandoff
  ];
  let onHotspot = false;
  let hostReachable = true;
  let lastSeenHost = window.performance.now();
  let isConnecting = false;
  let availableIp = false;
  let reachableIp = false;
  let { state = states.disabled } = $$props;
  function newRawConnectionInfo(rawInfo) {
    if (!rawInfo["active_connections"] == void 0 || !rawInfo["connection_details"]) {
      return;
    }
    onHotspot = rawInfo["over_hotspot"];
    availableIp = rawInfo["connection_details"].length > 0;
    updateState();
  }
  function newProbeResults(probeResults) {
    hostReachable = probeResults.some((probeResult) => {
      return probeResult.sameAsHost && probeResult.reachable;
    });
    if (hostReachable) {
      lastSeenHost = window.performance.now();
    }
    reachableIp = probeResults.some((probeResult) => {
      return !probeResult.sameAsHost && probeResult.reachable;
    });
    updateState();
  }
  function newAps(aps) {
    isConnecting = aps.some((ap) => {
      return ap.state == 1 || ap.state == 2;
    });
    console.log(isConnecting);
    console.log(aps);
  }
  function updateState() {
    let newState = state;
    if (state === states.disabled && onHotspot) {
      newState = states.hotspotFlowStart;
    }
    if (state === states.hotspotFlowStart && !isConnecting && !hostReachable && window.performance.now() - lastSeenHost > PROBE_TIMEOUT) {
      newState = states.disconnectedOnStart;
    }
    if (backToStartStates.includes(state) && hostReachable && onHotspot && !isConnecting && !availableIp) {
      newState = states.hotspotFlowStart;
    }
    if (state === states.hotspotFlowStart && isConnecting && !hostReachable && window.performance.now() - lastSeenHost > CHANNEL_SWITCH_START_TIMEOUT) {
      newState = states.channelSwitch;
    }
    if (state === states.channelSwitch && isConnecting && !hostReachable && window.performance.now() - lastSeenHost > CHANNEL_SWITCH_TIMEOUT) {
      newState = states.channelSwitchFailed;
    }
    if (ipAvailableSkipStates.includes(state) && onHotspot && availableIp) {
      newState = states.ipsReported;
    }
    if (state === states.ipsReported && !hostReachable && window.performance.now() - lastSeenHost > HOTSPOT_HANDOFF_TIMEOUT) {
      newState = states.hotspotHandoff;
    }
    if (state === states.hotspotHandoff && hostReachable && onHotspot && availableIp && !reachableIp) {
      newState = states.ipsReported;
    }
    if (ipReachableSkipStates.includes(state) && reachableIp) {
      newState = states.redirectImminent;
    }
    if (state === states.disabled && !hostReachable && window.performance.now() - lastSeenHost > PROBE_TIMEOUT) {
      newState = states.hostUnreachable;
    }
    if (state === states.hostUnreachable && hostReachable) {
      newState = states.disabled;
    }
    $$invalidate(0, state = newState);
    console.log(state);
  }
  onMount(() => {
    const updateInterval = setInterval(updateState, UPDATE_INTERVAL);
    return () => {
      clearInterval(updateInterval);
    };
  });
  $$self.$$set = ($$props2) => {
    if ("state" in $$props2)
      $$invalidate(0, state = $$props2.state);
  };
  return [state, newRawConnectionInfo, newProbeResults, newAps];
}
class StateMachine extends SvelteComponent {
  constructor(options) {
    super();
    init(this, options, instance$4, null, safe_not_equal, {
      state: 0,
      newRawConnectionInfo: 1,
      newProbeResults: 2,
      newAps: 3
    });
  }
  get newRawConnectionInfo() {
    return this.$$.ctx[1];
  }
  get newProbeResults() {
    return this.$$.ctx[2];
  }
  get newAps() {
    return this.$$.ctx[3];
  }
}
function create_else_block$2(ctx) {
  let div1;
  return {
    c() {
      div1 = element("div");
      div1.innerHTML = `<div class="col-auto pr-0 input-group-prepend"><span class="input-group-text bg-dark text-white">SSID</span></div> <input class="col form-control bg-dark text-white" type="text" name="ssid" value=""/>`;
      attr(div1, "class", "row pt-2 input-group");
    },
    m(target, anchor) {
      insert(target, div1, anchor);
    },
    p: noop,
    d(detaching) {
      if (detaching) {
        detach(div1);
      }
    }
  };
}
function create_if_block$2(ctx) {
  let input;
  let input_value_value;
  return {
    c() {
      input = element("input");
      attr(input, "type", "hidden");
      attr(input, "name", "ssid");
      input.value = input_value_value = /*ap*/
      ctx[0].ssid;
    },
    m(target, anchor) {
      insert(target, input, anchor);
    },
    p(ctx2, dirty) {
      if (dirty & /*ap*/
      1 && input_value_value !== (input_value_value = /*ap*/
      ctx2[0].ssid)) {
        input.value = input_value_value;
      }
    },
    d(detaching) {
      if (detaching) {
        detach(input);
      }
    }
  };
}
function create_fragment$2(ctx) {
  let form;
  let t0;
  let div1;
  let t3;
  let div3;
  let mounted;
  let dispose;
  function select_block_type(ctx2, dirty) {
    if (
      /*ap*/
      ctx2[0].ssid
    )
      return create_if_block$2;
    return create_else_block$2;
  }
  let current_block_type = select_block_type(ctx);
  let if_block = current_block_type(ctx);
  return {
    c() {
      form = element("form");
      if_block.c();
      t0 = space();
      div1 = element("div");
      div1.innerHTML = `<div class="col-auto pr-0 input-group-prepend"><span class="input-group-text bg-dark text-white">Password</span></div> <input class="col form-control bg-dark text-white" type="password" name="password" value=""/>`;
      t3 = space();
      div3 = element("div");
      div3.innerHTML = `<div class="col-sm-auto pr-0"><input class="btn btn-outline-light full-width" type="submit" value="Connect"/></div>`;
      attr(div1, "class", "row pt-2 input-group");
      attr(div3, "class", "row pt-2 pb-2 input-group");
      attr(form, "class", "container p-0");
      attr(form, "action", "/wifi/api/save");
      attr(form, "method", "post");
      attr(form, "data-action", actions.save);
    },
    m(target, anchor) {
      insert(target, form, anchor);
      if_block.m(form, null);
      append(form, t0);
      append(form, div1);
      append(form, t3);
      append(form, div3);
      if (!mounted) {
        dispose = listen(form, "submit", prevent_default(
          /*submit_handler*/
          ctx[2]
        ));
        mounted = true;
      }
    },
    p(ctx2, [dirty]) {
      if (current_block_type === (current_block_type = select_block_type(ctx2)) && if_block) {
        if_block.p(ctx2, dirty);
      } else {
        if_block.d(1);
        if_block = current_block_type(ctx2);
        if (if_block) {
          if_block.c();
          if_block.m(form, t0);
        }
      }
    },
    i: noop,
    o: noop,
    d(detaching) {
      if (detaching) {
        detach(form);
      }
      if_block.d();
      mounted = false;
      dispose();
    }
  };
}
function instance$3($$self, $$props, $$invalidate) {
  let { ap } = $$props;
  let { connectionChange } = $$props;
  const submit_handler = (e) => connectionChange(e, ap);
  $$self.$$set = ($$props2) => {
    if ("ap" in $$props2)
      $$invalidate(0, ap = $$props2.ap);
    if ("connectionChange" in $$props2)
      $$invalidate(1, connectionChange = $$props2.connectionChange);
  };
  return [ap, connectionChange, submit_handler];
}
class ConnectForm extends SvelteComponent {
  constructor(options) {
    super();
    init(this, options, instance$3, create_fragment$2, safe_not_equal, { ap: 0, connectionChange: 1 });
  }
}
const AP_FETCH_INTERVAL = 6e3;
const INFO_FETCH_INTERVAL = 2e3;
const PROBE_INTERVAL = 2500;
function instance$2($$self, $$props, $$invalidate) {
  let { aps = [] } = $$props;
  let { connectionDetails = [] } = $$props;
  let { rawConnectionInfo = {} } = $$props;
  let { probeDetails = [] } = $$props;
  let { probeResults = [] } = $$props;
  let isApSelected = false;
  let selectedAp = {};
  let probeTimeout = setTimeout(probeAll, PROBE_INTERVAL);
  function selectAp(ap) {
    if (selectedAp.ssid == ap.ssid) {
      return;
    }
    isApSelected = true;
    selectedAp = ap;
  }
  function processAps(receivedAps) {
    if (isApSelected) {
      let foundIndex = receivedAps.findIndex((ap) => ap.ssid == selectedAp.ssid);
      if (foundIndex != selectedAp.index) {
        if (foundIndex != -1) {
          receivedAps.splice(foundIndex, 1);
        }
        receivedAps.splice(selectedAp.index, 0, selectedAp);
      }
    }
    for (let i = 0; i < receivedAps.length; i++) {
      let ap = receivedAps[i];
      ap.index = i;
    }
    $$invalidate(0, aps = receivedAps);
  }
  function processConnectionInfo(info) {
    let newConnectionDetails = info["connection_details"];
    let protoProbes = [];
    newConnectionDetails.forEach((detail) => {
      let activeConnection = info["active_connections"].find((ac) => ac.interface == detail.interface);
      if (activeConnection) {
        detail.ssid = activeConnection.ssid;
      }
      protoProbes.push({ ip: detail.ip, detail });
    });
    if (protoProbes.find((e) => e.ip == window.location.hostname) === void 0) {
      protoProbes.push({ ip: window.location.hostname });
    }
    updateProbeDetails(protoProbes);
    $$invalidate(1, connectionDetails = newConnectionDetails);
    $$invalidate(2, rawConnectionInfo = info);
  }
  function updateProbeDetails(protoProbes) {
    var _a;
    let newProbeDetails = [];
    for (const protoProbe of protoProbes) {
      let detail = {
        ssid: (_a = protoProbe.detail) == null ? void 0 : _a.ssid,
        ip: protoProbe.ip,
        url: requestUrl.replace(window.location.hostname, protoProbe.ip),
        sameAsHost: window.location.hostname == protoProbe.ip,
        reachable: void 0
      };
      newProbeDetails.push(detail);
    }
    $$invalidate(3, probeDetails = newProbeDetails);
    clearTimeout(probeTimeout);
    probeAll();
  }
  async function fetchWifiList() {
    try {
      const response = await fetcher(requestUrl + "/wifi/api/ap_list");
      const data = await response.json();
      processAps(data.aps);
    } catch (error) {
      console.log(error);
    }
  }
  async function fetchConnectionInfo() {
    try {
      const response = await fetcher(requestUrl + "/wifi/api/connection_info");
      const data = await response.json();
      processConnectionInfo(data);
    } catch (error) {
      console.log(error);
    }
  }
  async function probeAll() {
    let newProbeResults = [];
    for (const probeDetail of probeDetails) {
      probeDetail.reachable = await probe(probeDetail.url);
      newProbeResults.push(probeDetail);
    }
    $$invalidate(4, probeResults = newProbeResults);
    probeTimeout = setTimeout(probeAll, PROBE_INTERVAL);
  }
  onMount(() => {
    const apInterval = setInterval(fetchWifiList, AP_FETCH_INTERVAL);
    const infoInterval = setInterval(fetchConnectionInfo, INFO_FETCH_INTERVAL);
    fetchWifiList();
    fetchConnectionInfo();
    return () => {
      clearInterval(apInterval);
      clearInterval(infoInterval);
    };
  });
  $$self.$$set = ($$props2) => {
    if ("aps" in $$props2)
      $$invalidate(0, aps = $$props2.aps);
    if ("connectionDetails" in $$props2)
      $$invalidate(1, connectionDetails = $$props2.connectionDetails);
    if ("rawConnectionInfo" in $$props2)
      $$invalidate(2, rawConnectionInfo = $$props2.rawConnectionInfo);
    if ("probeDetails" in $$props2)
      $$invalidate(3, probeDetails = $$props2.probeDetails);
    if ("probeResults" in $$props2)
      $$invalidate(4, probeResults = $$props2.probeResults);
  };
  return [
    aps,
    connectionDetails,
    rawConnectionInfo,
    probeDetails,
    probeResults,
    selectAp,
    fetchConnectionInfo
  ];
}
class Fetcher extends SvelteComponent {
  constructor(options) {
    super();
    init(this, options, instance$2, null, safe_not_equal, {
      aps: 0,
      connectionDetails: 1,
      rawConnectionInfo: 2,
      probeDetails: 3,
      probeResults: 4,
      selectAp: 5,
      fetchConnectionInfo: 6
    });
  }
  get selectAp() {
    return this.$$.ctx[5];
  }
  get fetchConnectionInfo() {
    return this.$$.ctx[6];
  }
}
function create_if_block_14(ctx) {
  let t;
  return {
    c() {
      t = text("Success");
    },
    m(target, anchor) {
      insert(target, t, anchor);
    },
    d(detaching) {
      if (detaching) {
        detach(t);
      }
    }
  };
}
function create_if_block_13(ctx) {
  let t;
  return {
    c() {
      t = text("Connected");
    },
    m(target, anchor) {
      insert(target, t, anchor);
    },
    d(detaching) {
      if (detaching) {
        detach(t);
      }
    }
  };
}
function create_if_block_12$1(ctx) {
  let t;
  return {
    c() {
      t = text("Connected, turning off hotspot");
    },
    m(target, anchor) {
      insert(target, t, anchor);
    },
    d(detaching) {
      if (detaching) {
        detach(t);
      }
    }
  };
}
function create_if_block_11$1(ctx) {
  let t;
  return {
    c() {
      t = text("Hotspot connection lost");
    },
    m(target, anchor) {
      insert(target, t, anchor);
    },
    d(detaching) {
      if (detaching) {
        detach(t);
      }
    }
  };
}
function create_if_block_10$1(ctx) {
  let t;
  return {
    c() {
      t = text("Please wait...");
    },
    m(target, anchor) {
      insert(target, t, anchor);
    },
    d(detaching) {
      if (detaching) {
        detach(t);
      }
    }
  };
}
function create_if_block_9$1(ctx) {
  let t;
  return {
    c() {
      t = text("Hotspot connection lost");
    },
    m(target, anchor) {
      insert(target, t, anchor);
    },
    d(detaching) {
      if (detaching) {
        detach(t);
      }
    }
  };
}
function create_if_block_8$1(ctx) {
  let t;
  return {
    c() {
      t = text("PrusaLink unreachable");
    },
    m(target, anchor) {
      insert(target, t, anchor);
    },
    d(detaching) {
      if (detaching) {
        detach(t);
      }
    }
  };
}
function create_if_block_7$1(ctx) {
  let ul;
  let li;
  let t0;
  let t1;
  let t2;
  return {
    c() {
      ul = element("ul");
      li = element("li");
      t0 = text("We'll redirect you to PrusaLinks new IP ");
      t1 = text(
        /*redirectIp*/
        ctx[2]
      );
      t2 = text(" shortly");
    },
    m(target, anchor) {
      insert(target, ul, anchor);
      append(ul, li);
      append(li, t0);
      append(li, t1);
      append(li, t2);
    },
    p(ctx2, dirty) {
      if (dirty & /*redirectIp*/
      4)
        set_data(
          t1,
          /*redirectIp*/
          ctx2[2]
        );
    },
    d(detaching) {
      if (detaching) {
        detach(ul);
      }
    }
  };
}
function create_if_block_6$1(ctx) {
  let ul;
  return {
    c() {
      ul = element("ul");
      ul.innerHTML = `<li>To continue please connect back to your local network</li> <li>It&#39;s possible this will happen automatically</li>`;
    },
    m(target, anchor) {
      insert(target, ul, anchor);
    },
    p: noop,
    d(detaching) {
      if (detaching) {
        detach(ul);
      }
    }
  };
}
function create_if_block_5$1(ctx) {
  let ul;
  return {
    c() {
      ul = element("ul");
      ul.innerHTML = `<li>Your device has probably disconnected from the hotspot. Please connect to it again</li>`;
    },
    m(target, anchor) {
      insert(target, ul, anchor);
    },
    p: noop,
    d(detaching) {
      if (detaching) {
        detach(ul);
      }
    }
  };
}
function create_if_block_4$1(ctx) {
  let ul;
  return {
    c() {
      ul = element("ul");
      ul.innerHTML = `<li>The connection process sometimes causes PrusaLink to be unresponsive for a bit.</li> <li>Everything is fine.jpg</li>`;
    },
    m(target, anchor) {
      insert(target, ul, anchor);
    },
    p: noop,
    d(detaching) {
      if (detaching) {
        detach(ul);
      }
    }
  };
}
function create_if_block_3$1(ctx) {
  let ul;
  return {
    c() {
      ul = element("ul");
      ul.innerHTML = `<li>The connection has been unexpectedly lost here&#39;s some stuff to check</li> <li>Are there multiple PrusaLinks in setup mode at the same time?</li> <li>Is your PrusaLink device still on?</li> <li>Are you in range of the hotspot?</li>`;
    },
    m(target, anchor) {
      insert(target, ul, anchor);
    },
    p: noop,
    d(detaching) {
      if (detaching) {
        detach(ul);
      }
    }
  };
}
function create_if_block_1$1(ctx) {
  let ul;
  let li;
  let t1;
  function select_block_type_2(ctx2, dirty) {
    if (
      /*availableIps*/
      ctx2[1].length > 0
    )
      return create_if_block_2$1;
    return create_else_block$1;
  }
  let current_block_type = select_block_type_2(ctx);
  let if_block = current_block_type(ctx);
  return {
    c() {
      ul = element("ul");
      li = element("li");
      li.textContent = "This webpage cannot connect to your PrusaLink";
      t1 = space();
      if_block.c();
    },
    m(target, anchor) {
      insert(target, ul, anchor);
      append(ul, li);
      append(ul, t1);
      if_block.m(ul, null);
    },
    p(ctx2, dirty) {
      if (current_block_type === (current_block_type = select_block_type_2(ctx2)) && if_block) {
        if_block.p(ctx2, dirty);
      } else {
        if_block.d(1);
        if_block = current_block_type(ctx2);
        if (if_block) {
          if_block.c();
          if_block.m(ul, null);
        }
      }
    },
    d(detaching) {
      if (detaching) {
        detach(ul);
      }
      if_block.d();
    }
  };
}
function create_else_block$1(ctx) {
  let li;
  return {
    c() {
      li = element("li");
      li.innerHTML = `If you see PrusaLink hotspot in available Wi-Fi networks, connect to it again and go to <a href="http://prusalink.local">prusalink.local</a>`;
    },
    m(target, anchor) {
      insert(target, li, anchor);
    },
    p: noop,
    d(detaching) {
      if (detaching) {
        detach(li);
      }
    }
  };
}
function create_if_block_2$1(ctx) {
  let li;
  let t0;
  let a;
  let t1_value = (
    /*availableIps*/
    ctx[1][0] + ""
  );
  let t1;
  let a_href_value;
  return {
    c() {
      li = element("li");
      t0 = text("It seems to be available on another ip address though: \n							");
      a = element("a");
      t1 = text(t1_value);
      attr(a, "href", a_href_value = window.location.href.replace(
        window.location.hostname,
        /*availableIps*/
        ctx[1][0]
      ));
    },
    m(target, anchor) {
      insert(target, li, anchor);
      append(li, t0);
      append(li, a);
      append(a, t1);
    },
    p(ctx2, dirty) {
      if (dirty & /*availableIps*/
      2 && t1_value !== (t1_value = /*availableIps*/
      ctx2[1][0] + ""))
        set_data(t1, t1_value);
      if (dirty & /*availableIps*/
      2 && a_href_value !== (a_href_value = window.location.href.replace(
        window.location.hostname,
        /*availableIps*/
        ctx2[1][0]
      ))) {
        attr(a, "href", a_href_value);
      }
    },
    d(detaching) {
      if (detaching) {
        detach(li);
      }
    }
  };
}
function create_if_block$1(ctx) {
  let div;
  let button;
  let div_transition;
  let current;
  let mounted;
  let dispose;
  return {
    c() {
      div = element("div");
      button = element("button");
      button.textContent = "Close";
      attr(button, "class", "btn btn-outline-light");
      attr(div, "class", "col");
    },
    m(target, anchor) {
      insert(target, div, anchor);
      append(div, button);
      current = true;
      if (!mounted) {
        dispose = listen(
          button,
          "click",
          /*hideDialog*/
          ctx[4]
        );
        mounted = true;
      }
    },
    p: noop,
    i(local) {
      if (current)
        return;
      if (local) {
        add_render_callback(() => {
          if (!current)
            return;
          if (!div_transition)
            div_transition = create_bidirectional_transition(div, fade, {}, true);
          div_transition.run(1);
        });
      }
      current = true;
    },
    o(local) {
      if (local) {
        if (!div_transition)
          div_transition = create_bidirectional_transition(div, fade, {}, false);
        div_transition.run(0);
      }
      current = false;
    },
    d(detaching) {
      if (detaching) {
        detach(div);
      }
      if (detaching && div_transition)
        div_transition.end();
      mounted = false;
      dispose();
    }
  };
}
function create_key_block(ctx) {
  let div5;
  let div1;
  let div0;
  let h2;
  let t0;
  let div3;
  let div2;
  let t1;
  let div4;
  let div5_transition;
  let current;
  let mounted;
  let dispose;
  function select_block_type(ctx2, dirty) {
    if (
      /*state*/
      ctx2[0] == states.hostUnreachable
    )
      return create_if_block_8$1;
    if (
      /*state*/
      ctx2[0] == states.disconnectedOnStart
    )
      return create_if_block_9$1;
    if (
      /*state*/
      ctx2[0] == states.channelSwitch
    )
      return create_if_block_10$1;
    if (
      /*state*/
      ctx2[0] == states.channelSwitchFailed
    )
      return create_if_block_11$1;
    if (
      /*state*/
      ctx2[0] == states.ipsReported
    )
      return create_if_block_12$1;
    if (
      /*state*/
      ctx2[0] == states.hotspotHandoff
    )
      return create_if_block_13;
    if (
      /*state*/
      ctx2[0] == states.redirectImminent
    )
      return create_if_block_14;
  }
  let current_block_type = select_block_type(ctx);
  let if_block0 = current_block_type && current_block_type(ctx);
  function select_block_type_1(ctx2, dirty) {
    if (
      /*state*/
      ctx2[0] == states.hostUnreachable
    )
      return create_if_block_1$1;
    if (
      /*state*/
      ctx2[0] == states.disconnectedOnStart
    )
      return create_if_block_3$1;
    if (
      /*state*/
      ctx2[0] == states.channelSwitch
    )
      return create_if_block_4$1;
    if (
      /*state*/
      ctx2[0] == states.channelSwitchFailed
    )
      return create_if_block_5$1;
    if (
      /*state*/
      ctx2[0] == states.ipsReported || /*state*/
      ctx2[0] == states.hotspotHandoff
    )
      return create_if_block_6$1;
    if (
      /*state*/
      ctx2[0] == states.redirectImminent
    )
      return create_if_block_7$1;
  }
  let current_block_type_1 = select_block_type_1(ctx);
  let if_block1 = current_block_type_1 && current_block_type_1(ctx);
  let if_block2 = (
    /*state*/
    ctx[0] == states.hostUnreachable && create_if_block$1(ctx)
  );
  return {
    c() {
      div5 = element("div");
      div1 = element("div");
      div0 = element("div");
      h2 = element("h2");
      if (if_block0)
        if_block0.c();
      t0 = space();
      div3 = element("div");
      div2 = element("div");
      if (if_block1)
        if_block1.c();
      t1 = space();
      div4 = element("div");
      if (if_block2)
        if_block2.c();
      attr(div0, "class", "col");
      attr(div1, "class", "row");
      attr(div2, "class", "col");
      attr(div3, "class", "row");
      attr(div4, "class", "row pt-3");
    },
    m(target, anchor) {
      insert(target, div5, anchor);
      append(div5, div1);
      append(div1, div0);
      append(div0, h2);
      if (if_block0)
        if_block0.m(h2, null);
      append(div5, t0);
      append(div5, div3);
      append(div3, div2);
      if (if_block1)
        if_block1.m(div2, null);
      append(div5, t1);
      append(div5, div4);
      if (if_block2)
        if_block2.m(div4, null);
      current = true;
      if (!mounted) {
        dispose = listen(div5, "click", stop_propagation(
          /*click_handler*/
          ctx[5]
        ));
        mounted = true;
      }
    },
    p(ctx2, dirty) {
      if (current_block_type !== (current_block_type = select_block_type(ctx2))) {
        if (if_block0)
          if_block0.d(1);
        if_block0 = current_block_type && current_block_type(ctx2);
        if (if_block0) {
          if_block0.c();
          if_block0.m(h2, null);
        }
      }
      if (current_block_type_1 === (current_block_type_1 = select_block_type_1(ctx2)) && if_block1) {
        if_block1.p(ctx2, dirty);
      } else {
        if (if_block1)
          if_block1.d(1);
        if_block1 = current_block_type_1 && current_block_type_1(ctx2);
        if (if_block1) {
          if_block1.c();
          if_block1.m(div2, null);
        }
      }
      if (
        /*state*/
        ctx2[0] == states.hostUnreachable
      ) {
        if (if_block2) {
          if_block2.p(ctx2, dirty);
          if (dirty & /*state*/
          1) {
            transition_in(if_block2, 1);
          }
        } else {
          if_block2 = create_if_block$1(ctx2);
          if_block2.c();
          transition_in(if_block2, 1);
          if_block2.m(div4, null);
        }
      } else if (if_block2) {
        group_outros();
        transition_out(if_block2, 1, 1, () => {
          if_block2 = null;
        });
        check_outros();
      }
    },
    i(local) {
      if (current)
        return;
      transition_in(if_block2);
      if (local) {
        add_render_callback(() => {
          if (!current)
            return;
          if (!div5_transition)
            div5_transition = create_bidirectional_transition(div5, slide, { axis: "x" }, true);
          div5_transition.run(1);
        });
      }
      current = true;
    },
    o(local) {
      transition_out(if_block2);
      if (local) {
        if (!div5_transition)
          div5_transition = create_bidirectional_transition(div5, slide, { axis: "x" }, false);
        div5_transition.run(0);
      }
      current = false;
    },
    d(detaching) {
      if (detaching) {
        detach(div5);
      }
      if (if_block0) {
        if_block0.d();
      }
      if (if_block1) {
        if_block1.d();
      }
      if (if_block2)
        if_block2.d();
      if (detaching && div5_transition)
        div5_transition.end();
      mounted = false;
      dispose();
    }
  };
}
function create_fragment$1(ctx) {
  let dialog_1;
  let previous_key = (
    /*state*/
    ctx[0]
  );
  let key_block = create_key_block(ctx);
  return {
    c() {
      dialog_1 = element("dialog");
      key_block.c();
      attr(dialog_1, "class", "border container p-4 svelte-1k7dgk4");
    },
    m(target, anchor) {
      insert(target, dialog_1, anchor);
      key_block.m(dialog_1, null);
      ctx[6](dialog_1);
    },
    p(ctx2, [dirty]) {
      if (dirty & /*state*/
      1 && safe_not_equal(previous_key, previous_key = /*state*/
      ctx2[0])) {
        group_outros();
        transition_out(key_block, 1, 1, noop);
        check_outros();
        key_block = create_key_block(ctx2);
        key_block.c();
        transition_in(key_block, 1);
        key_block.m(dialog_1, null);
      } else {
        key_block.p(ctx2, dirty);
      }
    },
    i(local) {
      transition_in(key_block);
    },
    o(local) {
      transition_out(key_block);
    },
    d(detaching) {
      if (detaching) {
        detach(dialog_1);
      }
      key_block.d(detaching);
      ctx[6](null);
    }
  };
}
function instance$1($$self, $$props, $$invalidate) {
  let { state } = $$props;
  let { availableIps } = $$props;
  let { redirectIp } = $$props;
  let dialog;
  const dialogShowStates = [
    states.disconnectedOnStart,
    states.channelSwitch,
    states.channelSwitchFailed,
    states.ipsReported,
    states.hotspotHandoff,
    states.redirectImminent,
    states.hostUnreachable
  ];
  function finishClosing() {
    dialog.classList.remove("hide");
    dialog.close();
    dialog.removeEventListener("animationend", finishClosing, false);
  }
  function hideDialog() {
    dialog.classList.add("hide");
    dialog.addEventListener("animationend", finishClosing, false);
  }
  function click_handler(event) {
    bubble.call(this, $$self, event);
  }
  function dialog_1_binding($$value) {
    binding_callbacks[$$value ? "unshift" : "push"](() => {
      dialog = $$value;
      $$invalidate(3, dialog);
    });
  }
  $$self.$$set = ($$props2) => {
    if ("state" in $$props2)
      $$invalidate(0, state = $$props2.state);
    if ("availableIps" in $$props2)
      $$invalidate(1, availableIps = $$props2.availableIps);
    if ("redirectIp" in $$props2)
      $$invalidate(2, redirectIp = $$props2.redirectIp);
  };
  $$self.$$.update = () => {
    if ($$self.$$.dirty & /*dialog, state*/
    9) {
      if (dialog && dialogShowStates.includes(state))
        dialog.showModal();
    }
    if ($$self.$$.dirty & /*dialog, state*/
    9) {
      if (dialog && dialog.open && !dialogShowStates.includes(state))
        hideDialog();
    }
  };
  return [
    state,
    availableIps,
    redirectIp,
    dialog,
    hideDialog,
    click_handler,
    dialog_1_binding
  ];
}
class Modal extends SvelteComponent {
  constructor(options) {
    super();
    init(this, options, instance$1, create_fragment$1, safe_not_equal, { state: 0, availableIps: 1, redirectIp: 2 });
  }
}
function get_each_context(ctx, list, i) {
  const child_ctx = ctx.slice();
  child_ctx[37] = list[i];
  return child_ctx;
}
function get_each_context_1(ctx, list, i) {
  const child_ctx = ctx.slice();
  child_ctx[40] = list[i];
  const constants_0 = (
    /*probeDetails*/
    child_ctx[8].find(function func(...args) {
      return (
        /*func*/
        ctx[24](
          /*connectionDetail*/
          child_ctx[40],
          ...args
        )
      );
    })
  );
  child_ctx[41] = constants_0;
  const constants_1 = (
    /*probeResults*/
    child_ctx[3].find(function func_1(...args) {
      return (
        /*func_1*/
        ctx[25](
          /*connectionDetail*/
          child_ctx[40],
          ...args
        )
      );
    })
  );
  child_ctx[42] = constants_1;
  return child_ctx;
}
function create_else_block_3(ctx) {
  let h2;
  return {
    c() {
      h2 = element("h2");
      h2.textContent = "PrusaLink is not connected to any LAN network";
    },
    m(target, anchor) {
      insert(target, h2, anchor);
    },
    p: noop,
    d(detaching) {
      if (detaching) {
        detach(h2);
      }
    }
  };
}
function create_if_block_8(ctx) {
  let h2;
  let t1;
  let div5;
  let div4;
  let t8;
  let each_value_1 = ensure_array_like(
    /*connectionDetails*/
    ctx[7]
  );
  let each_blocks = [];
  for (let i = 0; i < each_value_1.length; i += 1) {
    each_blocks[i] = create_each_block_1(get_each_context_1(ctx, each_value_1, i));
  }
  return {
    c() {
      h2 = element("h2");
      h2.textContent = "Connections";
      t1 = space();
      div5 = element("div");
      div4 = element("div");
      div4.innerHTML = `<div class="col-auto" style="width: 125px">Interface</div> <div class="col-auto" style="width: 175px">IP address</div> <div class="col">SSID</div> <div class="col-auto" style="width: 175px"></div>`;
      t8 = space();
      for (let i = 0; i < each_blocks.length; i += 1) {
        each_blocks[i].c();
      }
      set_style(h2, "margin-bottom", "0.3em");
      attr(div4, "class", "row border border-white pt-2 pb-2");
      attr(div5, "class", "container mb-5");
    },
    m(target, anchor) {
      insert(target, h2, anchor);
      insert(target, t1, anchor);
      insert(target, div5, anchor);
      append(div5, div4);
      append(div5, t8);
      for (let i = 0; i < each_blocks.length; i += 1) {
        if (each_blocks[i]) {
          each_blocks[i].m(div5, null);
        }
      }
    },
    p(ctx2, dirty) {
      if (dirty[0] & /*probeDetails, connectionDetails, probeResults*/
      392) {
        each_value_1 = ensure_array_like(
          /*connectionDetails*/
          ctx2[7]
        );
        let i;
        for (i = 0; i < each_value_1.length; i += 1) {
          const child_ctx = get_each_context_1(ctx2, each_value_1, i);
          if (each_blocks[i]) {
            each_blocks[i].p(child_ctx, dirty);
          } else {
            each_blocks[i] = create_each_block_1(child_ctx);
            each_blocks[i].c();
            each_blocks[i].m(div5, null);
          }
        }
        for (; i < each_blocks.length; i += 1) {
          each_blocks[i].d(1);
        }
        each_blocks.length = each_value_1.length;
      }
    },
    d(detaching) {
      if (detaching) {
        detach(h2);
        detach(t1);
        detach(div5);
      }
      destroy_each(each_blocks, detaching);
    }
  };
}
function create_if_block_12(ctx) {
  let t_value = (
    /*connectionDetail*/
    ctx[40].ssid + ""
  );
  let t;
  return {
    c() {
      t = text(t_value);
    },
    m(target, anchor) {
      insert(target, t, anchor);
    },
    p(ctx2, dirty) {
      if (dirty[0] & /*connectionDetails*/
      128 && t_value !== (t_value = /*connectionDetail*/
      ctx2[40].ssid + ""))
        set_data(t, t_value);
    },
    d(detaching) {
      if (detaching) {
        detach(t);
      }
    }
  };
}
function create_else_block_2(ctx) {
  let div;
  return {
    c() {
      div = element("div");
      div.textContent = "Unreachable";
      attr(div, "class", "float-right");
    },
    m(target, anchor) {
      insert(target, div, anchor);
    },
    p: noop,
    d(detaching) {
      if (detaching) {
        detach(div);
      }
    }
  };
}
function create_if_block_11(ctx) {
  let button;
  let mounted;
  let dispose;
  function click_handler() {
    return (
      /*click_handler*/
      ctx[23](
        /*probeResult*/
        ctx[42]
      )
    );
  }
  return {
    c() {
      button = element("button");
      button.textContent = "Go there";
      attr(button, "class", "btn btn-outline-light float-right");
    },
    m(target, anchor) {
      insert(target, button, anchor);
      if (!mounted) {
        dispose = listen(button, "click", click_handler);
        mounted = true;
      }
    },
    p(new_ctx, dirty) {
      ctx = new_ctx;
    },
    d(detaching) {
      if (detaching) {
        detach(button);
      }
      mounted = false;
      dispose();
    }
  };
}
function create_if_block_10(ctx) {
  let div1;
  return {
    c() {
      div1 = element("div");
      div1.innerHTML = `<div class="spinner-border text-light" role="status"><span class="sr-only">Please wait...</span></div>`;
      attr(div1, "class", "float-right");
    },
    m(target, anchor) {
      insert(target, div1, anchor);
    },
    p: noop,
    d(detaching) {
      if (detaching) {
        detach(div1);
      }
    }
  };
}
function create_if_block_9(ctx) {
  let div;
  return {
    c() {
      div = element("div");
      div.textContent = "You are here";
      attr(div, "class", "float-right");
    },
    m(target, anchor) {
      insert(target, div, anchor);
    },
    p: noop,
    d(detaching) {
      if (detaching) {
        detach(div);
      }
    }
  };
}
function create_each_block_1(ctx) {
  let div4;
  let div0;
  let t0_value = (
    /*connectionDetail*/
    ctx[40].interface + ""
  );
  let t0;
  let t1;
  let div1;
  let t2_value = (
    /*connectionDetail*/
    ctx[40].ip + ""
  );
  let t2;
  let t3;
  let div2;
  let t4;
  let div3;
  let t5;
  let if_block0 = (
    /*connectionDetail*/
    ctx[40].ssid && create_if_block_12(ctx)
  );
  function select_block_type_1(ctx2, dirty) {
    if (
      /*probeDetail*/
      ctx2[41].sameAsHost
    )
      return create_if_block_9;
    if (
      /*probeResult*/
      ctx2[42] == void 0
    )
      return create_if_block_10;
    if (
      /*probeResult*/
      ctx2[42].reachable == true
    )
      return create_if_block_11;
    return create_else_block_2;
  }
  let current_block_type = select_block_type_1(ctx);
  let if_block1 = current_block_type(ctx);
  return {
    c() {
      div4 = element("div");
      div0 = element("div");
      t0 = text(t0_value);
      t1 = space();
      div1 = element("div");
      t2 = text(t2_value);
      t3 = space();
      div2 = element("div");
      if (if_block0)
        if_block0.c();
      t4 = space();
      div3 = element("div");
      if_block1.c();
      t5 = space();
      attr(div0, "class", "col-auto");
      set_style(div0, "width", "125px");
      attr(div1, "class", "col-auto");
      set_style(div1, "width", "175px");
      attr(div2, "class", "col");
      attr(div3, "class", "col-auto");
      set_style(div3, "width", "175px");
      attr(div4, "class", "row border border-white border-top-0 pt-2 pb-2");
    },
    m(target, anchor) {
      insert(target, div4, anchor);
      append(div4, div0);
      append(div0, t0);
      append(div4, t1);
      append(div4, div1);
      append(div1, t2);
      append(div4, t3);
      append(div4, div2);
      if (if_block0)
        if_block0.m(div2, null);
      append(div4, t4);
      append(div4, div3);
      if_block1.m(div3, null);
      append(div4, t5);
    },
    p(ctx2, dirty) {
      if (dirty[0] & /*connectionDetails*/
      128 && t0_value !== (t0_value = /*connectionDetail*/
      ctx2[40].interface + ""))
        set_data(t0, t0_value);
      if (dirty[0] & /*connectionDetails*/
      128 && t2_value !== (t2_value = /*connectionDetail*/
      ctx2[40].ip + ""))
        set_data(t2, t2_value);
      if (
        /*connectionDetail*/
        ctx2[40].ssid
      ) {
        if (if_block0) {
          if_block0.p(ctx2, dirty);
        } else {
          if_block0 = create_if_block_12(ctx2);
          if_block0.c();
          if_block0.m(div2, null);
        }
      } else if (if_block0) {
        if_block0.d(1);
        if_block0 = null;
      }
      if (current_block_type === (current_block_type = select_block_type_1(ctx2)) && if_block1) {
        if_block1.p(ctx2, dirty);
      } else {
        if_block1.d(1);
        if_block1 = current_block_type(ctx2);
        if (if_block1) {
          if_block1.c();
          if_block1.m(div3, null);
        }
      }
    },
    d(detaching) {
      if (detaching) {
        detach(div4);
      }
      if (if_block0)
        if_block0.d();
      if_block1.d();
    }
  };
}
function create_if_block_7(ctx) {
  let t;
  return {
    c() {
      t = text("Saved");
    },
    m(target, anchor) {
      insert(target, t, anchor);
    },
    p: noop,
    d(detaching) {
      if (detaching) {
        detach(t);
      }
    }
  };
}
function create_if_block_3(ctx) {
  let if_block_anchor;
  function select_block_type_3(ctx2, dirty) {
    if (
      /*ap*/
      ctx2[37].state == 1
    )
      return create_if_block_4;
    if (
      /*ap*/
      ctx2[37].state == 2
    )
      return create_if_block_5;
    if (
      /*ap*/
      ctx2[37].state == 3
    )
      return create_if_block_6;
  }
  let current_block_type = select_block_type_3(ctx);
  let if_block = current_block_type && current_block_type(ctx);
  return {
    c() {
      if (if_block)
        if_block.c();
      if_block_anchor = empty();
    },
    m(target, anchor) {
      if (if_block)
        if_block.m(target, anchor);
      insert(target, if_block_anchor, anchor);
    },
    p(ctx2, dirty) {
      if (current_block_type !== (current_block_type = select_block_type_3(ctx2))) {
        if (if_block)
          if_block.d(1);
        if_block = current_block_type && current_block_type(ctx2);
        if (if_block) {
          if_block.c();
          if_block.m(if_block_anchor.parentNode, if_block_anchor);
        }
      }
    },
    d(detaching) {
      if (detaching) {
        detach(if_block_anchor);
      }
      if (if_block) {
        if_block.d(detaching);
      }
    }
  };
}
function create_if_block_6(ctx) {
  let t;
  return {
    c() {
      t = text("Disconnecting");
    },
    m(target, anchor) {
      insert(target, t, anchor);
    },
    d(detaching) {
      if (detaching) {
        detach(t);
      }
    }
  };
}
function create_if_block_5(ctx) {
  let t;
  return {
    c() {
      t = text("Connected");
    },
    m(target, anchor) {
      insert(target, t, anchor);
    },
    d(detaching) {
      if (detaching) {
        detach(t);
      }
    }
  };
}
function create_if_block_4(ctx) {
  let t;
  return {
    c() {
      t = text("Connecting");
    },
    m(target, anchor) {
      insert(target, t, anchor);
    },
    d(detaching) {
      if (detaching) {
        detach(t);
      }
    }
  };
}
function create_if_block(ctx) {
  let div2;
  let div0;
  let t0;
  let current_block_type_index;
  let if_block;
  let t1;
  let div1;
  let div2_transition;
  let current;
  const if_block_creators = [create_if_block_1, create_else_block_1];
  const if_blocks = [];
  function select_block_type_4(ctx2, dirty) {
    if (
      /*ap*/
      ctx2[37].saved
    )
      return 0;
    return 1;
  }
  current_block_type_index = select_block_type_4(ctx);
  if_block = if_blocks[current_block_type_index] = if_block_creators[current_block_type_index](ctx);
  return {
    c() {
      div2 = element("div");
      div0 = element("div");
      t0 = space();
      if_block.c();
      t1 = space();
      div1 = element("div");
      attr(div0, "class", "col-auto");
      set_style(div0, "width", "60px");
      attr(div1, "class", "col-md");
      attr(div2, "class", "container row");
    },
    m(target, anchor) {
      insert(target, div2, anchor);
      append(div2, div0);
      append(div2, t0);
      if_blocks[current_block_type_index].m(div2, null);
      append(div2, t1);
      append(div2, div1);
      current = true;
    },
    p(ctx2, dirty) {
      let previous_block_index = current_block_type_index;
      current_block_type_index = select_block_type_4(ctx2);
      if (current_block_type_index === previous_block_index) {
        if_blocks[current_block_type_index].p(ctx2, dirty);
      } else {
        group_outros();
        transition_out(if_blocks[previous_block_index], 1, 1, () => {
          if_blocks[previous_block_index] = null;
        });
        check_outros();
        if_block = if_blocks[current_block_type_index];
        if (!if_block) {
          if_block = if_blocks[current_block_type_index] = if_block_creators[current_block_type_index](ctx2);
          if_block.c();
        } else {
          if_block.p(ctx2, dirty);
        }
        transition_in(if_block, 1);
        if_block.m(div2, t1);
      }
    },
    i(local) {
      if (current)
        return;
      transition_in(if_block);
      if (local) {
        add_render_callback(() => {
          if (!current)
            return;
          if (!div2_transition)
            div2_transition = create_bidirectional_transition(div2, slide, {}, true);
          div2_transition.run(1);
        });
      }
      current = true;
    },
    o(local) {
      transition_out(if_block);
      if (local) {
        if (!div2_transition)
          div2_transition = create_bidirectional_transition(div2, slide, {}, false);
        div2_transition.run(0);
      }
      current = false;
    },
    d(detaching) {
      if (detaching) {
        detach(div2);
      }
      if_blocks[current_block_type_index].d();
      if (detaching && div2_transition)
        div2_transition.end();
    }
  };
}
function create_else_block_1(ctx) {
  let div;
  let connectform;
  let current;
  connectform = new ConnectForm({
    props: {
      ap: (
        /*ap*/
        ctx[37]
      ),
      connectionChange: (
        /*connectionChange*/
        ctx[14]
      )
    }
  });
  return {
    c() {
      div = element("div");
      create_component(connectform.$$.fragment);
      attr(div, "class", "col-md-5 col-lg-4 col container");
    },
    m(target, anchor) {
      insert(target, div, anchor);
      mount_component(connectform, div, null);
      current = true;
    },
    p(ctx2, dirty) {
      const connectform_changes = {};
      if (dirty[0] & /*aps*/
      2)
        connectform_changes.ap = /*ap*/
        ctx2[37];
      connectform.$set(connectform_changes);
    },
    i(local) {
      if (current)
        return;
      transition_in(connectform.$$.fragment, local);
      current = true;
    },
    o(local) {
      transition_out(connectform.$$.fragment, local);
      current = false;
    },
    d(detaching) {
      if (detaching) {
        detach(div);
      }
      destroy_component(connectform);
    }
  };
}
function create_if_block_1(ctx) {
  let div1;
  let div0;
  let t0;
  let form;
  let input0;
  let input0_value_value;
  let t1;
  let input1;
  let mounted;
  let dispose;
  function select_block_type_5(ctx2, dirty) {
    if (
      /*ap*/
      ctx2[37].state >= 1 && /*ap*/
      ctx2[37].state <= 3
    )
      return create_if_block_2;
    return create_else_block;
  }
  let current_block_type = select_block_type_5(ctx);
  let if_block = current_block_type(ctx);
  function submit_handler_2(...args) {
    return (
      /*submit_handler_2*/
      ctx[29](
        /*ap*/
        ctx[37],
        ...args
      )
    );
  }
  return {
    c() {
      div1 = element("div");
      div0 = element("div");
      if_block.c();
      t0 = space();
      form = element("form");
      input0 = element("input");
      t1 = space();
      input1 = element("input");
      attr(input0, "type", "hidden");
      attr(input0, "name", "ssid");
      input0.value = input0_value_value = /*ap*/
      ctx[37].ssid;
      attr(input1, "class", "btn btn-outline-light");
      attr(input1, "type", "submit");
      input1.value = "Forget";
      attr(form, "class", "col-auto");
      attr(form, "action", "/wifi/api/forget");
      attr(form, "method", "post");
      attr(form, "data-action", actions.forget);
      attr(div0, "class", "row pt-2 pd-2 input-group");
      attr(div1, "class", "col container");
    },
    m(target, anchor) {
      insert(target, div1, anchor);
      append(div1, div0);
      if_block.m(div0, null);
      append(div0, t0);
      append(div0, form);
      append(form, input0);
      append(form, t1);
      append(form, input1);
      if (!mounted) {
        dispose = listen(form, "submit", prevent_default(submit_handler_2));
        mounted = true;
      }
    },
    p(new_ctx, dirty) {
      ctx = new_ctx;
      if (current_block_type === (current_block_type = select_block_type_5(ctx)) && if_block) {
        if_block.p(ctx, dirty);
      } else {
        if_block.d(1);
        if_block = current_block_type(ctx);
        if (if_block) {
          if_block.c();
          if_block.m(div0, t0);
        }
      }
      if (dirty[0] & /*aps*/
      2 && input0_value_value !== (input0_value_value = /*ap*/
      ctx[37].ssid)) {
        input0.value = input0_value_value;
      }
    },
    i: noop,
    o: noop,
    d(detaching) {
      if (detaching) {
        detach(div1);
      }
      if_block.d();
      mounted = false;
      dispose();
    }
  };
}
function create_else_block(ctx) {
  let form;
  let input0;
  let input0_value_value;
  let t;
  let input1;
  let mounted;
  let dispose;
  function submit_handler_1(...args) {
    return (
      /*submit_handler_1*/
      ctx[28](
        /*ap*/
        ctx[37],
        ...args
      )
    );
  }
  return {
    c() {
      form = element("form");
      input0 = element("input");
      t = space();
      input1 = element("input");
      attr(input0, "type", "hidden");
      attr(input0, "name", "ssid");
      input0.value = input0_value_value = /*ap*/
      ctx[37].ssid;
      attr(input1, "class", "btn btn-outline-light");
      attr(input1, "type", "submit");
      input1.value = "Connect";
      attr(form, "class", "col-auto");
      attr(form, "action", "/wifi/api/connect");
      attr(form, "method", "post");
      attr(form, "data-action", actions.connect);
    },
    m(target, anchor) {
      insert(target, form, anchor);
      append(form, input0);
      append(form, t);
      append(form, input1);
      if (!mounted) {
        dispose = listen(form, "submit", prevent_default(submit_handler_1));
        mounted = true;
      }
    },
    p(new_ctx, dirty) {
      ctx = new_ctx;
      if (dirty[0] & /*aps*/
      2 && input0_value_value !== (input0_value_value = /*ap*/
      ctx[37].ssid)) {
        input0.value = input0_value_value;
      }
    },
    d(detaching) {
      if (detaching) {
        detach(form);
      }
      mounted = false;
      dispose();
    }
  };
}
function create_if_block_2(ctx) {
  let form;
  let input0;
  let input0_value_value;
  let t;
  let input1;
  let mounted;
  let dispose;
  function submit_handler(...args) {
    return (
      /*submit_handler*/
      ctx[27](
        /*ap*/
        ctx[37],
        ...args
      )
    );
  }
  return {
    c() {
      form = element("form");
      input0 = element("input");
      t = space();
      input1 = element("input");
      attr(input0, "type", "hidden");
      attr(input0, "name", "ssid");
      input0.value = input0_value_value = /*ap*/
      ctx[37].ssid;
      attr(input1, "class", "btn btn-outline-light");
      attr(input1, "type", "submit");
      input1.value = "Disconnect";
      attr(form, "class", "col-auto");
      attr(form, "action", "/wifi/api/disconnect");
      attr(form, "method", "post");
      attr(form, "data-action", actions.disconnect);
    },
    m(target, anchor) {
      insert(target, form, anchor);
      append(form, input0);
      append(form, t);
      append(form, input1);
      if (!mounted) {
        dispose = listen(form, "submit", prevent_default(submit_handler));
        mounted = true;
      }
    },
    p(new_ctx, dirty) {
      ctx = new_ctx;
      if (dirty[0] & /*aps*/
      2 && input0_value_value !== (input0_value_value = /*ap*/
      ctx[37].ssid)) {
        input0.value = input0_value_value;
      }
    },
    d(detaching) {
      if (detaching) {
        detach(form);
      }
      mounted = false;
      dispose();
    }
  };
}
function create_each_block(key_1, ctx) {
  let div6;
  let div0;
  let img;
  let img_src_value;
  let img_alt_value;
  let t0;
  let div1;
  let span0;
  let t1_value = (
    /*ap*/
    (ctx[37].ssid ? (
      /*ap*/
      ctx[37].ssid
    ) : "[hidden]") + ""
  );
  let t1;
  let t2;
  let div2;
  let span1;
  let div2_class_value;
  let t3;
  let div3;
  let span2;
  let t4_value = (
    /*ap*/
    ctx[37].frequency + ""
  );
  let t4;
  let t5;
  let div4;
  let t7;
  let div5;
  let t8;
  let t9;
  let div6_transition;
  let current;
  let mounted;
  let dispose;
  function click_handler_1() {
    return (
      /*click_handler_1*/
      ctx[26](
        /*ap*/
        ctx[37]
      )
    );
  }
  function select_block_type_2(ctx2, dirty) {
    if (
      /*ap*/
      ctx2[37].state >= 1 && /*ap*/
      ctx2[37].state <= 3
    )
      return create_if_block_3;
    if (
      /*ap*/
      ctx2[37].saved
    )
      return create_if_block_7;
  }
  let current_block_type = select_block_type_2(ctx);
  let if_block0 = current_block_type && current_block_type(ctx);
  let if_block1 = (
    /*selectedSSID*/
    ctx[9] === /*ap*/
    ctx[37].ssid && create_if_block(ctx)
  );
  function click_handler_2() {
    return (
      /*click_handler_2*/
      ctx[30](
        /*ap*/
        ctx[37]
      )
    );
  }
  return {
    key: key_1,
    first: null,
    c() {
      div6 = element("div");
      div0 = element("div");
      img = element("img");
      t0 = space();
      div1 = element("div");
      span0 = element("span");
      t1 = text(t1_value);
      t2 = space();
      div2 = element("div");
      span1 = element("span");
      if (if_block0)
        if_block0.c();
      t3 = space();
      div3 = element("div");
      span2 = element("span");
      t4 = text(t4_value);
      t5 = space();
      div4 = element("div");
      div4.innerHTML = `<button class="btn btn-outline-light float-right">Details</button>`;
      t7 = space();
      div5 = element("div");
      t8 = space();
      if (if_block1)
        if_block1.c();
      t9 = space();
      attr(img, "height", "25");
      if (!src_url_equal(img.src, img_src_value = "img/" + /*ap*/
      ctx[37].strength_icon))
        attr(img, "src", img_src_value);
      attr(img, "alt", img_alt_value = /*ap*/
      ctx[37].strength_icon);
      attr(div0, "class", "col-auto");
      set_style(div0, "width", "60px");
      attr(div1, "class", "col text-break");
      attr(div2, "class", div2_class_value = "col-auto " + /*ap*/
      (ctx[37].state == 2 ? "text-white" : ""));
      set_style(div2, "width", "150px");
      attr(div3, "class", "col-auto");
      set_style(div3, "width", "125px");
      attr(div4, "class", "col-auto");
      set_style(div4, "width", "150px");
      attr(div5, "class", "w-100");
      attr(div6, "class", "row border border-white border-top-0 pt-2 pb-2");
      this.first = div6;
    },
    m(target, anchor) {
      insert(target, div6, anchor);
      append(div6, div0);
      append(div0, img);
      append(div6, t0);
      append(div6, div1);
      append(div1, span0);
      append(span0, t1);
      append(div6, t2);
      append(div6, div2);
      append(div2, span1);
      if (if_block0)
        if_block0.m(span1, null);
      append(div6, t3);
      append(div6, div3);
      append(div3, span2);
      append(span2, t4);
      append(div6, t5);
      append(div6, div4);
      append(div6, t7);
      append(div6, div5);
      append(div6, t8);
      if (if_block1)
        if_block1.m(div6, null);
      append(div6, t9);
      current = true;
      if (!mounted) {
        dispose = [
          listen(div1, "click", click_handler_1),
          listen(div6, "click", stop_propagation(click_handler_2))
        ];
        mounted = true;
      }
    },
    p(new_ctx, dirty) {
      ctx = new_ctx;
      if (!current || dirty[0] & /*aps*/
      2 && !src_url_equal(img.src, img_src_value = "img/" + /*ap*/
      ctx[37].strength_icon)) {
        attr(img, "src", img_src_value);
      }
      if (!current || dirty[0] & /*aps*/
      2 && img_alt_value !== (img_alt_value = /*ap*/
      ctx[37].strength_icon)) {
        attr(img, "alt", img_alt_value);
      }
      if ((!current || dirty[0] & /*aps*/
      2) && t1_value !== (t1_value = /*ap*/
      (ctx[37].ssid ? (
        /*ap*/
        ctx[37].ssid
      ) : "[hidden]") + ""))
        set_data(t1, t1_value);
      if (current_block_type === (current_block_type = select_block_type_2(ctx)) && if_block0) {
        if_block0.p(ctx, dirty);
      } else {
        if (if_block0)
          if_block0.d(1);
        if_block0 = current_block_type && current_block_type(ctx);
        if (if_block0) {
          if_block0.c();
          if_block0.m(span1, null);
        }
      }
      if (!current || dirty[0] & /*aps*/
      2 && div2_class_value !== (div2_class_value = "col-auto " + /*ap*/
      (ctx[37].state == 2 ? "text-white" : ""))) {
        attr(div2, "class", div2_class_value);
      }
      if ((!current || dirty[0] & /*aps*/
      2) && t4_value !== (t4_value = /*ap*/
      ctx[37].frequency + ""))
        set_data(t4, t4_value);
      if (
        /*selectedSSID*/
        ctx[9] === /*ap*/
        ctx[37].ssid
      ) {
        if (if_block1) {
          if_block1.p(ctx, dirty);
          if (dirty[0] & /*selectedSSID, aps*/
          514) {
            transition_in(if_block1, 1);
          }
        } else {
          if_block1 = create_if_block(ctx);
          if_block1.c();
          transition_in(if_block1, 1);
          if_block1.m(div6, t9);
        }
      } else if (if_block1) {
        group_outros();
        transition_out(if_block1, 1, 1, () => {
          if_block1 = null;
        });
        check_outros();
      }
    },
    i(local) {
      if (current)
        return;
      transition_in(if_block1);
      if (local) {
        add_render_callback(() => {
          if (!current)
            return;
          if (!div6_transition)
            div6_transition = create_bidirectional_transition(div6, slide, {}, true);
          div6_transition.run(1);
        });
      }
      current = true;
    },
    o(local) {
      transition_out(if_block1);
      if (local) {
        if (!div6_transition)
          div6_transition = create_bidirectional_transition(div6, slide, {}, false);
        div6_transition.run(0);
      }
      current = false;
    },
    d(detaching) {
      if (detaching) {
        detach(div6);
      }
      if (if_block0) {
        if_block0.d();
      }
      if (if_block1)
        if_block1.d();
      if (detaching && div6_transition)
        div6_transition.end();
      mounted = false;
      run_all(dispose);
    }
  };
}
function create_fragment(ctx) {
  let fetcher_1;
  let updating_aps;
  let updating_connectionDetails;
  let updating_rawConnectionInfo;
  let updating_probeDetails;
  let updating_probeResults;
  let t0;
  let statemachine;
  let updating_state;
  let t1;
  let h1;
  let t3;
  let t4;
  let h20;
  let t6;
  let div7;
  let div6;
  let t15;
  let each_blocks = [];
  let each_1_lookup = /* @__PURE__ */ new Map();
  let t16;
  let h21;
  let t18;
  let div11;
  let div10;
  let div8;
  let connectform;
  let t19;
  let div9;
  let div11_transition;
  let t20;
  let div14;
  let div13;
  let div12;
  let button;
  let t22;
  let modal_1;
  let current;
  let mounted;
  let dispose;
  function fetcher_1_aps_binding(value) {
    ctx[16](value);
  }
  function fetcher_1_connectionDetails_binding(value) {
    ctx[17](value);
  }
  function fetcher_1_rawConnectionInfo_binding(value) {
    ctx[18](value);
  }
  function fetcher_1_probeDetails_binding(value) {
    ctx[19](value);
  }
  function fetcher_1_probeResults_binding(value) {
    ctx[20](value);
  }
  let fetcher_1_props = {};
  if (
    /*aps*/
    ctx[1] !== void 0
  ) {
    fetcher_1_props.aps = /*aps*/
    ctx[1];
  }
  if (
    /*connectionDetails*/
    ctx[7] !== void 0
  ) {
    fetcher_1_props.connectionDetails = /*connectionDetails*/
    ctx[7];
  }
  if (
    /*rawConnectionInfo*/
    ctx[2] !== void 0
  ) {
    fetcher_1_props.rawConnectionInfo = /*rawConnectionInfo*/
    ctx[2];
  }
  if (
    /*probeDetails*/
    ctx[8] !== void 0
  ) {
    fetcher_1_props.probeDetails = /*probeDetails*/
    ctx[8];
  }
  if (
    /*probeResults*/
    ctx[3] !== void 0
  ) {
    fetcher_1_props.probeResults = /*probeResults*/
    ctx[3];
  }
  fetcher_1 = new Fetcher({ props: fetcher_1_props });
  ctx[15](fetcher_1);
  binding_callbacks.push(() => bind(fetcher_1, "aps", fetcher_1_aps_binding));
  binding_callbacks.push(() => bind(fetcher_1, "connectionDetails", fetcher_1_connectionDetails_binding));
  binding_callbacks.push(() => bind(fetcher_1, "rawConnectionInfo", fetcher_1_rawConnectionInfo_binding));
  binding_callbacks.push(() => bind(fetcher_1, "probeDetails", fetcher_1_probeDetails_binding));
  binding_callbacks.push(() => bind(fetcher_1, "probeResults", fetcher_1_probeResults_binding));
  function statemachine_state_binding(value) {
    ctx[22](value);
  }
  let statemachine_props = {};
  if (
    /*state*/
    ctx[4] !== void 0
  ) {
    statemachine_props.state = /*state*/
    ctx[4];
  }
  statemachine = new StateMachine({ props: statemachine_props });
  ctx[21](statemachine);
  binding_callbacks.push(() => bind(statemachine, "state", statemachine_state_binding));
  function select_block_type(ctx2, dirty) {
    var _a;
    if (
      /*connectionDetails*/
      (_a = ctx2[7]) == null ? void 0 : _a.length
    )
      return create_if_block_8;
    return create_else_block_3;
  }
  let current_block_type = select_block_type(ctx);
  let if_block = current_block_type(ctx);
  let each_value = ensure_array_like(
    /*aps*/
    ctx[1]
  );
  const get_key = (ctx2) => (
    /*ap*/
    ctx2[37].ssid
  );
  for (let i = 0; i < each_value.length; i += 1) {
    let child_ctx = get_each_context(ctx, each_value, i);
    let key = get_key(child_ctx);
    each_1_lookup.set(key, each_blocks[i] = create_each_block(key, child_ctx));
  }
  connectform = new ConnectForm({
    props: {
      ap: {},
      connectionChange: (
        /*connectionChange*/
        ctx[14]
      )
    }
  });
  let modal_1_props = {
    availableIps: (
      /*availableIps*/
      ctx[10]
    ),
    redirectIp: (
      /*redirectIp*/
      ctx[11]
    ),
    state: (
      /*state*/
      ctx[4]
    )
  };
  modal_1 = new Modal({ props: modal_1_props });
  ctx[31](modal_1);
  return {
    c() {
      create_component(fetcher_1.$$.fragment);
      t0 = space();
      create_component(statemachine.$$.fragment);
      t1 = space();
      h1 = element("h1");
      h1.textContent = "Wi-Fi Setup";
      t3 = space();
      if_block.c();
      t4 = space();
      h20 = element("h2");
      h20.textContent = "Available networks";
      t6 = space();
      div7 = element("div");
      div6 = element("div");
      div6.innerHTML = `<div class="col-auto" style="width: 60px;"></div> <div class="col"><span>SSID</span></div> <div class="col-auto" style="width: 150px">State</div> <div class="col-auto" style="width: 125px"><span>Frequency</span></div> <div class="col-auto" style="width: 150px"></div> <div class="w-100"></div>`;
      t15 = space();
      for (let i = 0; i < each_blocks.length; i += 1) {
        each_blocks[i].c();
      }
      t16 = space();
      h21 = element("h2");
      h21.textContent = "Connect to another network";
      t18 = space();
      div11 = element("div");
      div10 = element("div");
      div8 = element("div");
      create_component(connectform.$$.fragment);
      t19 = space();
      div9 = element("div");
      div9.innerHTML = ``;
      t20 = space();
      div14 = element("div");
      div13 = element("div");
      div12 = element("div");
      button = element("button");
      button.innerHTML = `Back to wizard <img src="img/arrow-left.svg" height="16" alt="back arrow"/>`;
      t22 = space();
      create_component(modal_1.$$.fragment);
      attr(h1, "class", "align-center");
      set_style(h20, "margin-bottom", "0.3em");
      attr(div6, "class", "row border border-white pt-2 pb-2");
      attr(div7, "class", "container mb-5");
      set_style(h21, "margin-bottom", "0.2em");
      attr(div8, "class", "col-lg-4 col");
      attr(div9, "class", "col-lg");
      attr(div10, "class", "row");
      attr(div11, "class", "container p-0");
      attr(button, "class", "btn btn-outline-light full-width");
      attr(div12, "class", "col-sm-auto p-0");
      attr(div13, "class", "row");
      attr(div14, "class", "container navigation");
    },
    m(target, anchor) {
      mount_component(fetcher_1, target, anchor);
      insert(target, t0, anchor);
      mount_component(statemachine, target, anchor);
      insert(target, t1, anchor);
      insert(target, h1, anchor);
      insert(target, t3, anchor);
      if_block.m(target, anchor);
      insert(target, t4, anchor);
      insert(target, h20, anchor);
      insert(target, t6, anchor);
      insert(target, div7, anchor);
      append(div7, div6);
      append(div7, t15);
      for (let i = 0; i < each_blocks.length; i += 1) {
        if (each_blocks[i]) {
          each_blocks[i].m(div7, null);
        }
      }
      insert(target, t16, anchor);
      insert(target, h21, anchor);
      insert(target, t18, anchor);
      insert(target, div11, anchor);
      append(div11, div10);
      append(div10, div8);
      mount_component(connectform, div8, null);
      append(div10, t19);
      append(div10, div9);
      insert(target, t20, anchor);
      insert(target, div14, anchor);
      append(div14, div13);
      append(div13, div12);
      append(div12, button);
      insert(target, t22, anchor);
      mount_component(modal_1, target, anchor);
      current = true;
      if (!mounted) {
        dispose = listen(
          button,
          "click",
          /*backToWizard*/
          ctx[13]
        );
        mounted = true;
      }
    },
    p(ctx2, dirty) {
      const fetcher_1_changes = {};
      if (!updating_aps && dirty[0] & /*aps*/
      2) {
        updating_aps = true;
        fetcher_1_changes.aps = /*aps*/
        ctx2[1];
        add_flush_callback(() => updating_aps = false);
      }
      if (!updating_connectionDetails && dirty[0] & /*connectionDetails*/
      128) {
        updating_connectionDetails = true;
        fetcher_1_changes.connectionDetails = /*connectionDetails*/
        ctx2[7];
        add_flush_callback(() => updating_connectionDetails = false);
      }
      if (!updating_rawConnectionInfo && dirty[0] & /*rawConnectionInfo*/
      4) {
        updating_rawConnectionInfo = true;
        fetcher_1_changes.rawConnectionInfo = /*rawConnectionInfo*/
        ctx2[2];
        add_flush_callback(() => updating_rawConnectionInfo = false);
      }
      if (!updating_probeDetails && dirty[0] & /*probeDetails*/
      256) {
        updating_probeDetails = true;
        fetcher_1_changes.probeDetails = /*probeDetails*/
        ctx2[8];
        add_flush_callback(() => updating_probeDetails = false);
      }
      if (!updating_probeResults && dirty[0] & /*probeResults*/
      8) {
        updating_probeResults = true;
        fetcher_1_changes.probeResults = /*probeResults*/
        ctx2[3];
        add_flush_callback(() => updating_probeResults = false);
      }
      fetcher_1.$set(fetcher_1_changes);
      const statemachine_changes = {};
      if (!updating_state && dirty[0] & /*state*/
      16) {
        updating_state = true;
        statemachine_changes.state = /*state*/
        ctx2[4];
        add_flush_callback(() => updating_state = false);
      }
      statemachine.$set(statemachine_changes);
      if (current_block_type === (current_block_type = select_block_type(ctx2)) && if_block) {
        if_block.p(ctx2, dirty);
      } else {
        if_block.d(1);
        if_block = current_block_type(ctx2);
        if (if_block) {
          if_block.c();
          if_block.m(t4.parentNode, t4);
        }
      }
      if (dirty[0] & /*selectAp, aps, connectionChange, selectedSSID*/
      20994) {
        each_value = ensure_array_like(
          /*aps*/
          ctx2[1]
        );
        group_outros();
        each_blocks = update_keyed_each(each_blocks, dirty, get_key, 1, ctx2, each_value, each_1_lookup, div7, outro_and_destroy_block, create_each_block, null, get_each_context);
        check_outros();
      }
      const modal_1_changes = {};
      if (dirty[0] & /*availableIps*/
      1024)
        modal_1_changes.availableIps = /*availableIps*/
        ctx2[10];
      if (dirty[0] & /*redirectIp*/
      2048)
        modal_1_changes.redirectIp = /*redirectIp*/
        ctx2[11];
      if (dirty[0] & /*state*/
      16)
        modal_1_changes.state = /*state*/
        ctx2[4];
      modal_1.$set(modal_1_changes);
    },
    i(local) {
      if (current)
        return;
      transition_in(fetcher_1.$$.fragment, local);
      transition_in(statemachine.$$.fragment, local);
      for (let i = 0; i < each_value.length; i += 1) {
        transition_in(each_blocks[i]);
      }
      transition_in(connectform.$$.fragment, local);
      if (local) {
        add_render_callback(() => {
          if (!current)
            return;
          if (!div11_transition)
            div11_transition = create_bidirectional_transition(div11, slide, {}, true);
          div11_transition.run(1);
        });
      }
      transition_in(modal_1.$$.fragment, local);
      current = true;
    },
    o(local) {
      transition_out(fetcher_1.$$.fragment, local);
      transition_out(statemachine.$$.fragment, local);
      for (let i = 0; i < each_blocks.length; i += 1) {
        transition_out(each_blocks[i]);
      }
      transition_out(connectform.$$.fragment, local);
      if (local) {
        if (!div11_transition)
          div11_transition = create_bidirectional_transition(div11, slide, {}, false);
        div11_transition.run(0);
      }
      transition_out(modal_1.$$.fragment, local);
      current = false;
    },
    d(detaching) {
      if (detaching) {
        detach(t0);
        detach(t1);
        detach(h1);
        detach(t3);
        detach(t4);
        detach(h20);
        detach(t6);
        detach(div7);
        detach(t16);
        detach(h21);
        detach(t18);
        detach(div11);
        detach(t20);
        detach(div14);
        detach(t22);
      }
      ctx[15](null);
      destroy_component(fetcher_1, detaching);
      ctx[21](null);
      destroy_component(statemachine, detaching);
      if_block.d(detaching);
      for (let i = 0; i < each_blocks.length; i += 1) {
        each_blocks[i].d();
      }
      destroy_component(connectform);
      if (detaching && div11_transition)
        div11_transition.end();
      ctx[31](null);
      destroy_component(modal_1, detaching);
      mounted = false;
      dispose();
    }
  };
}
const actions = {
  save: "save",
  connect: "connect",
  disconnect: "disconnect",
  forget: "forget"
};
const REDIRECT_DELAY = 5e3;
function instance($$self, $$props, $$invalidate) {
  const hotspotOffStates = [states.ipsReported, states.hotspotHandoff, states.redirectImminent];
  let modal;
  let fetcher2;
  let stateMachine;
  let aps = [];
  let connectionDetails;
  let rawConnectionInfo;
  let probeDetails;
  let probeResults;
  let state;
  let selectedSSID;
  let availableIps;
  let redirectIp;
  function selectAp(ap) {
    $$invalidate(9, selectedSSID = ap.ssid);
    fetcher2.selectAp(ap);
  }
  function backToWizard() {
    window.location.href = requestUrl + "/wizard";
  }
  function connectionChange(e, apToUpdate) {
    let action = e.target.getAttribute("data-action");
    let newAps = aps;
    let index = newAps.indexOf(apToUpdate);
    switch (action) {
      case actions.save:
      case actions.connect:
        if ((apToUpdate == null ? void 0 : apToUpdate.state) == 0) {
          newAps[index].state = 1;
        }
        break;
      case actions.disconnect:
      case actions.forget:
        if ((apToUpdate == null ? void 0 : apToUpdate.state) == 2) {
          newAps[index].state = 3;
        }
        break;
      default:
        console.log("data-action was not found action: " + action);
    }
    $$invalidate(1, aps = newAps);
    handleFormData(e);
    setTimeout(fetcher2.fetchConnectionInfo, 300);
  }
  async function autoRedirect(probeDetail) {
    let reachable = await probe(probeDetail.url);
    if (reachable) {
      changeHost(probeDetail.url);
    } else {
      $$invalidate(11, redirectIp = void 0);
    }
  }
  async function redirectAttempt(receivedProbeResulte) {
    if (redirectIp !== void 0) {
      return;
    }
    if (state != states.redirectImminent) {
      return;
    }
    let potentialRedirectProbe;
    for (const probeResult of receivedProbeResulte) {
      if (probeResult.reachable && !probeResult.sameAsHost) {
        potentialRedirectProbe = probeResult;
        break;
      }
    }
    if (potentialRedirectProbe == void 0) {
      return;
    }
    let reachable = await probe(potentialRedirectProbe.url);
    if (reachable) {
      setTimeout(
        () => {
          autoRedirect(potentialRedirectProbe);
        },
        REDIRECT_DELAY
      );
      $$invalidate(11, redirectIp = potentialRedirectProbe.ip);
    }
  }
  function fillAvailableIps(receivedProbeResults) {
    let newAvailable = [];
    for (const probeResult of receivedProbeResults) {
      if (probeResult.ip && !probeResult.sameAsHost) {
        newAvailable.push(probeResult.ip);
      }
    }
    $$invalidate(10, availableIps = newAvailable);
  }
  function fetcher_1_binding($$value) {
    binding_callbacks[$$value ? "unshift" : "push"](() => {
      fetcher2 = $$value;
      $$invalidate(6, fetcher2);
    });
  }
  function fetcher_1_aps_binding(value) {
    aps = value;
    $$invalidate(1, aps);
  }
  function fetcher_1_connectionDetails_binding(value) {
    connectionDetails = value;
    $$invalidate(7, connectionDetails);
  }
  function fetcher_1_rawConnectionInfo_binding(value) {
    rawConnectionInfo = value;
    $$invalidate(2, rawConnectionInfo);
  }
  function fetcher_1_probeDetails_binding(value) {
    probeDetails = value;
    $$invalidate(8, probeDetails);
  }
  function fetcher_1_probeResults_binding(value) {
    probeResults = value;
    $$invalidate(3, probeResults);
  }
  function statemachine_binding($$value) {
    binding_callbacks[$$value ? "unshift" : "push"](() => {
      stateMachine = $$value;
      $$invalidate(0, stateMachine);
    });
  }
  function statemachine_state_binding(value) {
    state = value;
    $$invalidate(4, state);
  }
  const click_handler = (probeResult) => changeHost(probeResult.url);
  const func = (connectionDetail, e) => e.ip == connectionDetail.ip;
  const func_1 = (connectionDetail, e) => e.ip == connectionDetail.ip;
  const click_handler_1 = (ap) => {
    selectAp(ap);
  };
  const submit_handler = (ap, e) => {
    connectionChange(e, ap);
  };
  const submit_handler_1 = (ap, e) => {
    connectionChange(e, ap);
  };
  const submit_handler_2 = (ap, e) => {
    connectionChange(e, ap);
  };
  const click_handler_2 = (ap) => {
    selectAp(ap);
  };
  function modal_1_binding($$value) {
    binding_callbacks[$$value ? "unshift" : "push"](() => {
      modal = $$value;
      $$invalidate(5, modal);
    });
  }
  $$self.$$.update = () => {
    if ($$self.$$.dirty[0] & /*state, rawConnectionInfo*/
    20) {
      if (hotspotOffStates.includes(state) && (rawConnectionInfo == null ? void 0 : rawConnectionInfo.hotspot_on))
        turnOffHotspot();
    }
    if ($$self.$$.dirty[0] & /*stateMachine, rawConnectionInfo*/
    5) {
      stateMachine == null ? void 0 : stateMachine.newRawConnectionInfo(rawConnectionInfo);
    }
    if ($$self.$$.dirty[0] & /*stateMachine, probeResults*/
    9) {
      stateMachine == null ? void 0 : stateMachine.newProbeResults(probeResults);
    }
    if ($$self.$$.dirty[0] & /*stateMachine, aps*/
    3) {
      stateMachine == null ? void 0 : stateMachine.newAps(aps);
    }
    if ($$self.$$.dirty[0] & /*probeResults*/
    8) {
      if (probeResults) {
        redirectAttempt(probeResults);
        fillAvailableIps(probeResults);
      }
    }
  };
  return [
    stateMachine,
    aps,
    rawConnectionInfo,
    probeResults,
    state,
    modal,
    fetcher2,
    connectionDetails,
    probeDetails,
    selectedSSID,
    availableIps,
    redirectIp,
    selectAp,
    backToWizard,
    connectionChange,
    fetcher_1_binding,
    fetcher_1_aps_binding,
    fetcher_1_connectionDetails_binding,
    fetcher_1_rawConnectionInfo_binding,
    fetcher_1_probeDetails_binding,
    fetcher_1_probeResults_binding,
    statemachine_binding,
    statemachine_state_binding,
    click_handler,
    func,
    func_1,
    click_handler_1,
    submit_handler,
    submit_handler_1,
    submit_handler_2,
    click_handler_2,
    modal_1_binding
  ];
}
class App extends SvelteComponent {
  constructor(options) {
    super();
    init(this, options, instance, create_fragment, safe_not_equal, {}, null, [-1, -1]);
  }
}
new App({
  target: document.getElementById("app")
});
//# sourceMappingURL=index-_7SIublR.js.map
