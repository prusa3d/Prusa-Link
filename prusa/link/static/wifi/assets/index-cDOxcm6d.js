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
function assign(tar, src) {
  for (const k in src)
    tar[k] = src[k];
  return (
    /** @type {T & S} */
    tar
  );
}
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
function create_slot(definition, ctx, $$scope, fn) {
  if (definition) {
    const slot_ctx = get_slot_context(definition, ctx, $$scope, fn);
    return definition[0](slot_ctx);
  }
}
function get_slot_context(definition, ctx, $$scope, fn) {
  return definition[1] && fn ? assign($$scope.ctx.slice(), definition[1](fn(ctx))) : $$scope.ctx;
}
function get_slot_changes(definition, $$scope, dirty, fn) {
  if (definition[2] && fn) {
    const lets = definition[2](fn(dirty));
    if ($$scope.dirty === void 0) {
      return lets;
    }
    if (typeof lets === "object") {
      const merged = [];
      const len = Math.max($$scope.dirty.length, lets.length);
      for (let i = 0; i < len; i += 1) {
        merged[i] = $$scope.dirty[i] | lets[i];
      }
      return merged;
    }
    return $$scope.dirty | lets;
  }
  return $$scope.dirty;
}
function update_slot_base(slot, slot_definition, ctx, $$scope, slot_changes, get_slot_context_fn) {
  if (slot_changes) {
    const slot_context = get_slot_context(slot_definition, ctx, $$scope, get_slot_context_fn);
    slot.p(slot_context, slot_changes);
  }
}
function get_all_dirty_from_scope($$scope) {
  if ($$scope.ctx.length > 32) {
    const dirty = [];
    const length = $$scope.ctx.length / 32;
    for (let i = 0; i < length; i++) {
      dirty[i] = -1;
    }
    return dirty;
  }
  return -1;
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
    fetch(`${ACTION_URL}?${data}`);
  } else {
    fetch(ACTION_URL, {
      method: "POST",
      body: data
    });
  }
}
function saveSsid(e, ap) {
  ap.savedSsid = e.target.value;
}
function savePassword(e, ap) {
  ap.savedPassword = e.target.value;
}
const TIMEOUT = 3e4;
class AutoRedirect {
  constructor(redirectCallback) {
    this.active = false;
    this.timeout = null;
    this.callback = redirectCallback;
  }
  update() {
    if (window.performance.now() - this.stateChangedAt > TIMEOUT) {
      this.disable();
    }
  }
  lookForRedirect(probeDetails) {
    if (!this.active) {
      return;
    }
    for (const probeDetail of probeDetails) {
      if (probeDetail.reachable && !probeDetail.sameAsHost) {
        this.callback(probeDetail.url);
        this.disable();
      }
    }
  }
  activate() {
    if (!this.active) {
      return;
    }
    this.stateChangedAt = window.performance.now();
    this.active = true;
  }
  disable() {
    clearTimeout(this.timeout);
    this.active = false;
  }
}
function create_else_block$1(ctx) {
  let div1;
  let div0;
  let t1;
  let input;
  let input_value_value;
  let input_autofocus_value;
  let mounted;
  let dispose;
  return {
    c() {
      div1 = element("div");
      div0 = element("div");
      div0.innerHTML = `<span class="input-group-text bg-dark text-white">SSID</span>`;
      t1 = space();
      input = element("input");
      attr(div0, "class", "col-auto pr-0 input-group-prepend");
      attr(input, "class", "col form-control bg-dark text-white");
      attr(input, "type", "text");
      attr(input, "name", "ssid");
      input.value = input_value_value = /*ap*/
      ctx[0].savedSsid ? (
        /*ap*/
        ctx[0].savedSsid
      ) : "";
      input.autofocus = input_autofocus_value = /*ap*/
      ctx[0].ssidFocus;
      attr(div1, "class", "row pt-2 input-group");
    },
    m(target, anchor) {
      insert(target, div1, anchor);
      append(div1, div0);
      append(div1, t1);
      append(div1, input);
      if (
        /*ap*/
        ctx[0].ssidFocus
      )
        input.focus();
      if (!mounted) {
        dispose = [
          listen(
            input,
            "input",
            /*input_handler*/
            ctx[1]
          ),
          listen(
            input,
            "focusin",
            /*focusin_handler*/
            ctx[2]
          ),
          listen(
            input,
            "focusout",
            /*focusout_handler*/
            ctx[3]
          )
        ];
        mounted = true;
      }
    },
    p(ctx2, dirty) {
      if (dirty & /*ap*/
      1 && input_value_value !== (input_value_value = /*ap*/
      ctx2[0].savedSsid ? (
        /*ap*/
        ctx2[0].savedSsid
      ) : "") && input.value !== input_value_value) {
        input.value = input_value_value;
      }
      if (dirty & /*ap*/
      1 && input_autofocus_value !== (input_autofocus_value = /*ap*/
      ctx2[0].ssidFocus)) {
        input.autofocus = input_autofocus_value;
      }
    },
    d(detaching) {
      if (detaching) {
        detach(div1);
      }
      mounted = false;
      run_all(dispose);
    }
  };
}
function create_if_block$1(ctx) {
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
  let div0;
  let t2;
  let input0;
  let input0_value_value;
  let input0_autofocus_value;
  let t3;
  let div3;
  let mounted;
  let dispose;
  function select_block_type(ctx2, dirty) {
    if (
      /*ap*/
      ctx2[0].ssid
    )
      return create_if_block$1;
    return create_else_block$1;
  }
  let current_block_type = select_block_type(ctx);
  let if_block = current_block_type(ctx);
  return {
    c() {
      form = element("form");
      if_block.c();
      t0 = space();
      div1 = element("div");
      div0 = element("div");
      div0.innerHTML = `<span class="input-group-text bg-dark text-white">Password</span>`;
      t2 = space();
      input0 = element("input");
      t3 = space();
      div3 = element("div");
      div3.innerHTML = `<div class="col-sm-auto pr-0"><input class="btn btn-outline-light full-width" type="submit" value="Connect"/></div>`;
      attr(div0, "class", "col-auto pr-0 input-group-prepend");
      attr(input0, "class", "col form-control bg-dark text-white");
      attr(input0, "type", "password");
      attr(input0, "name", "password");
      input0.value = input0_value_value = /*ap*/
      ctx[0].savedPassword ? (
        /*ap*/
        ctx[0].savedPassword
      ) : "";
      input0.autofocus = input0_autofocus_value = /*ap*/
      ctx[0].passFocus;
      attr(div1, "class", "row pt-2 input-group");
      attr(div3, "class", "row pt-2 pb-2 input-group");
      attr(form, "class", "container p-0");
      attr(form, "action", "/save");
      attr(form, "method", "post");
    },
    m(target, anchor) {
      insert(target, form, anchor);
      if_block.m(form, null);
      append(form, t0);
      append(form, div1);
      append(div1, div0);
      append(div1, t2);
      append(div1, input0);
      append(form, t3);
      append(form, div3);
      if (
        /*ap*/
        ctx[0].passFocus
      )
        input0.focus();
      if (!mounted) {
        dispose = [
          listen(
            input0,
            "input",
            /*input_handler_1*/
            ctx[4]
          ),
          listen(
            input0,
            "focusin",
            /*focusin_handler_1*/
            ctx[5]
          ),
          listen(
            input0,
            "focusout",
            /*focusout_handler_1*/
            ctx[6]
          ),
          listen(form, "submit", prevent_default(handleFormData))
        ];
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
      if (dirty & /*ap*/
      1 && input0_value_value !== (input0_value_value = /*ap*/
      ctx2[0].savedPassword ? (
        /*ap*/
        ctx2[0].savedPassword
      ) : "") && input0.value !== input0_value_value) {
        input0.value = input0_value_value;
      }
      if (dirty & /*ap*/
      1 && input0_autofocus_value !== (input0_autofocus_value = /*ap*/
      ctx2[0].passFocus)) {
        input0.autofocus = input0_autofocus_value;
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
      run_all(dispose);
    }
  };
}
function instance$2($$self, $$props, $$invalidate) {
  let { ap } = $$props;
  const input_handler = (e) => saveSsid(e, ap);
  const focusin_handler = () => $$invalidate(0, ap.ssidFocus = true, ap);
  const focusout_handler = () => $$invalidate(0, ap.ssidFocus = false, ap);
  const input_handler_1 = (e) => savePassword(e, ap);
  const focusin_handler_1 = () => $$invalidate(0, ap.passFocus = true, ap);
  const focusout_handler_1 = () => $$invalidate(0, ap.passFocus = false, ap);
  $$self.$$set = ($$props2) => {
    if ("ap" in $$props2)
      $$invalidate(0, ap = $$props2.ap);
  };
  return [
    ap,
    input_handler,
    focusin_handler,
    focusout_handler,
    input_handler_1,
    focusin_handler_1,
    focusout_handler_1
  ];
}
class ConnectForm extends SvelteComponent {
  constructor(options) {
    super();
    init(this, options, instance$2, create_fragment$2, safe_not_equal, { ap: 0 });
  }
}
const get_navigation_slot_changes = (dirty) => ({});
const get_navigation_slot_context = (ctx) => ({});
const get_content_slot_changes = (dirty) => ({});
const get_content_slot_context = (ctx) => ({});
const get_header_slot_changes = (dirty) => ({});
const get_header_slot_context = (ctx) => ({});
function create_fragment$1(ctx) {
  let dialog_1;
  let div3;
  let div0;
  let t0;
  let div1;
  let t1;
  let div2;
  let current;
  let mounted;
  let dispose;
  const header_slot_template = (
    /*#slots*/
    ctx[3].header
  );
  const header_slot = create_slot(
    header_slot_template,
    ctx,
    /*$$scope*/
    ctx[2],
    get_header_slot_context
  );
  const content_slot_template = (
    /*#slots*/
    ctx[3].content
  );
  const content_slot = create_slot(
    content_slot_template,
    ctx,
    /*$$scope*/
    ctx[2],
    get_content_slot_context
  );
  const navigation_slot_template = (
    /*#slots*/
    ctx[3].navigation
  );
  const navigation_slot = create_slot(
    navigation_slot_template,
    ctx,
    /*$$scope*/
    ctx[2],
    get_navigation_slot_context
  );
  return {
    c() {
      dialog_1 = element("dialog");
      div3 = element("div");
      div0 = element("div");
      if (header_slot)
        header_slot.c();
      t0 = space();
      div1 = element("div");
      if (content_slot)
        content_slot.c();
      t1 = space();
      div2 = element("div");
      if (navigation_slot)
        navigation_slot.c();
      attr(div0, "class", "row");
      attr(div1, "class", "row");
      attr(div2, "class", "row pt-3");
      attr(dialog_1, "class", "border container p-4 svelte-1w8ed2v");
    },
    m(target, anchor) {
      insert(target, dialog_1, anchor);
      append(dialog_1, div3);
      append(div3, div0);
      if (header_slot) {
        header_slot.m(div0, null);
      }
      append(div3, t0);
      append(div3, div1);
      if (content_slot) {
        content_slot.m(div1, null);
      }
      append(div3, t1);
      append(div3, div2);
      if (navigation_slot) {
        navigation_slot.m(div2, null);
      }
      ctx[5](dialog_1);
      current = true;
      if (!mounted) {
        dispose = [
          listen(div3, "click", stop_propagation(
            /*click_handler*/
            ctx[4]
          )),
          listen(
            dialog_1,
            "close",
            /*close_handler*/
            ctx[6]
          )
        ];
        mounted = true;
      }
    },
    p(ctx2, [dirty]) {
      if (header_slot) {
        if (header_slot.p && (!current || dirty & /*$$scope*/
        4)) {
          update_slot_base(
            header_slot,
            header_slot_template,
            ctx2,
            /*$$scope*/
            ctx2[2],
            !current ? get_all_dirty_from_scope(
              /*$$scope*/
              ctx2[2]
            ) : get_slot_changes(
              header_slot_template,
              /*$$scope*/
              ctx2[2],
              dirty,
              get_header_slot_changes
            ),
            get_header_slot_context
          );
        }
      }
      if (content_slot) {
        if (content_slot.p && (!current || dirty & /*$$scope*/
        4)) {
          update_slot_base(
            content_slot,
            content_slot_template,
            ctx2,
            /*$$scope*/
            ctx2[2],
            !current ? get_all_dirty_from_scope(
              /*$$scope*/
              ctx2[2]
            ) : get_slot_changes(
              content_slot_template,
              /*$$scope*/
              ctx2[2],
              dirty,
              get_content_slot_changes
            ),
            get_content_slot_context
          );
        }
      }
      if (navigation_slot) {
        if (navigation_slot.p && (!current || dirty & /*$$scope*/
        4)) {
          update_slot_base(
            navigation_slot,
            navigation_slot_template,
            ctx2,
            /*$$scope*/
            ctx2[2],
            !current ? get_all_dirty_from_scope(
              /*$$scope*/
              ctx2[2]
            ) : get_slot_changes(
              navigation_slot_template,
              /*$$scope*/
              ctx2[2],
              dirty,
              get_navigation_slot_changes
            ),
            get_navigation_slot_context
          );
        }
      }
    },
    i(local) {
      if (current)
        return;
      transition_in(header_slot, local);
      transition_in(content_slot, local);
      transition_in(navigation_slot, local);
      current = true;
    },
    o(local) {
      transition_out(header_slot, local);
      transition_out(content_slot, local);
      transition_out(navigation_slot, local);
      current = false;
    },
    d(detaching) {
      if (detaching) {
        detach(dialog_1);
      }
      if (header_slot)
        header_slot.d(detaching);
      if (content_slot)
        content_slot.d(detaching);
      if (navigation_slot)
        navigation_slot.d(detaching);
      ctx[5](null);
      mounted = false;
      run_all(dispose);
    }
  };
}
function instance$1($$self, $$props, $$invalidate) {
  let { $$slots: slots = {}, $$scope } = $$props;
  let { showModal } = $$props;
  let { dialog } = $$props;
  function click_handler(event) {
    bubble.call(this, $$self, event);
  }
  function dialog_1_binding($$value) {
    binding_callbacks[$$value ? "unshift" : "push"](() => {
      dialog = $$value;
      $$invalidate(1, dialog);
    });
  }
  const close_handler = () => $$invalidate(0, showModal = false);
  $$self.$$set = ($$props2) => {
    if ("showModal" in $$props2)
      $$invalidate(0, showModal = $$props2.showModal);
    if ("dialog" in $$props2)
      $$invalidate(1, dialog = $$props2.dialog);
    if ("$$scope" in $$props2)
      $$invalidate(2, $$scope = $$props2.$$scope);
  };
  $$self.$$.update = () => {
    if ($$self.$$.dirty & /*dialog, showModal*/
    3) {
      if (dialog && showModal)
        dialog.showModal();
    }
  };
  return [
    showModal,
    dialog,
    $$scope,
    slots,
    click_handler,
    dialog_1_binding,
    close_handler
  ];
}
class Modal extends SvelteComponent {
  constructor(options) {
    super();
    init(this, options, instance$1, create_fragment$1, safe_not_equal, { showModal: 0, dialog: 1 });
  }
}
function get_each_context(ctx, list, i) {
  const child_ctx = ctx.slice();
  child_ctx[25] = list[i];
  return child_ctx;
}
function get_each_context_1(ctx, list, i) {
  const child_ctx = ctx.slice();
  child_ctx[28] = list[i];
  const constants_0 = (
    /*probeDetails*/
    child_ctx[6].find(function func(...args) {
      return (
        /*func*/
        ctx[10](
          /*connectionDetail*/
          child_ctx[28],
          ...args
        )
      );
    })
  );
  child_ctx[29] = constants_0;
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
    ctx[1]
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
      if (dirty[0] & /*probeDetails, connectionDetails*/
      66) {
        each_value_1 = ensure_array_like(
          /*connectionDetails*/
          ctx2[1]
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
    ctx[28].ssid + ""
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
      2 && t_value !== (t_value = /*connectionDetail*/
      ctx2[28].ssid + ""))
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
function create_if_block_10(ctx) {
  let button;
  let mounted;
  let dispose;
  function click_handler() {
    return (
      /*click_handler*/
      ctx[9](
        /*probeDetail*/
        ctx[29]
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
    ctx[28].interface + ""
  );
  let t0;
  let t1;
  let div1;
  let t2_value = (
    /*connectionDetail*/
    ctx[28].ip + ""
  );
  let t2;
  let t3;
  let div2;
  let t4;
  let div3;
  let t5;
  let if_block0 = (
    /*connectionDetail*/
    ctx[28].ssid && create_if_block_12(ctx)
  );
  function select_block_type_1(ctx2, dirty) {
    if (
      /*probeDetail*/
      ctx2[29].sameAsHost
    )
      return create_if_block_9;
    if (
      /*probeDetail*/
      ctx2[29].reachable == true
    )
      return create_if_block_10;
    if (
      /*probeDetail*/
      ctx2[29].reachable == void 0
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
      2 && t0_value !== (t0_value = /*connectionDetail*/
      ctx2[28].interface + ""))
        set_data(t0, t0_value);
      if (dirty[0] & /*connectionDetails*/
      2 && t2_value !== (t2_value = /*connectionDetail*/
      ctx2[28].ip + ""))
        set_data(t2, t2_value);
      if (
        /*connectionDetail*/
        ctx2[28].ssid
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
      ctx2[25].state == 1
    )
      return create_if_block_4;
    if (
      /*ap*/
      ctx2[25].state == 2
    )
      return create_if_block_5;
    if (
      /*ap*/
      ctx2[25].state == 3
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
      ctx2[25].saved
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
  connectform = new ConnectForm({ props: { ap: (
    /*selectedAp*/
    ctx[3]
  ) } });
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
      if (dirty[0] & /*selectedAp*/
      8)
        connectform_changes.ap = /*selectedAp*/
        ctx2[3];
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
      ctx2[25].state >= 1 && /*ap*/
      ctx2[25].state <= 3
    )
      return create_if_block_2;
    return create_else_block;
  }
  let current_block_type = select_block_type_5(ctx);
  let if_block = current_block_type(ctx);
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
      ctx[25].ssid;
      attr(input1, "class", "btn btn-outline-light");
      attr(input1, "type", "submit");
      input1.value = "Forget";
      attr(form, "class", "col-auto");
      attr(form, "action", "/wifi/api/forget");
      attr(form, "method", "post");
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
        dispose = listen(form, "submit", prevent_default(handleFormData));
        mounted = true;
      }
    },
    p(ctx2, dirty) {
      if (current_block_type === (current_block_type = select_block_type_5(ctx2)) && if_block) {
        if_block.p(ctx2, dirty);
      } else {
        if_block.d(1);
        if_block = current_block_type(ctx2);
        if (if_block) {
          if_block.c();
          if_block.m(div0, t0);
        }
      }
      if (dirty[0] & /*aps*/
      1 && input0_value_value !== (input0_value_value = /*ap*/
      ctx2[25].ssid)) {
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
  return {
    c() {
      form = element("form");
      input0 = element("input");
      t = space();
      input1 = element("input");
      attr(input0, "type", "hidden");
      attr(input0, "name", "ssid");
      input0.value = input0_value_value = /*ap*/
      ctx[25].ssid;
      attr(input1, "class", "btn btn-outline-light");
      attr(input1, "type", "submit");
      input1.value = "Connect";
      attr(form, "class", "col-auto");
      attr(form, "action", "/wifi/api/connect");
      attr(form, "method", "post");
    },
    m(target, anchor) {
      insert(target, form, anchor);
      append(form, input0);
      append(form, t);
      append(form, input1);
      if (!mounted) {
        dispose = listen(form, "submit", prevent_default(handleFormData));
        mounted = true;
      }
    },
    p(ctx2, dirty) {
      if (dirty[0] & /*aps*/
      1 && input0_value_value !== (input0_value_value = /*ap*/
      ctx2[25].ssid)) {
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
  return {
    c() {
      form = element("form");
      input0 = element("input");
      t = space();
      input1 = element("input");
      attr(input0, "type", "hidden");
      attr(input0, "name", "ssid");
      input0.value = input0_value_value = /*ap*/
      ctx[25].ssid;
      attr(input1, "class", "btn btn-outline-light");
      attr(input1, "type", "submit");
      input1.value = "Disconnect";
      attr(form, "class", "col-auto");
      attr(form, "action", "/wifi/api/disconnect");
      attr(form, "method", "post");
    },
    m(target, anchor) {
      insert(target, form, anchor);
      append(form, input0);
      append(form, t);
      append(form, input1);
      if (!mounted) {
        dispose = listen(form, "submit", prevent_default(handleFormData));
        mounted = true;
      }
    },
    p(ctx2, dirty) {
      if (dirty[0] & /*aps*/
      1 && input0_value_value !== (input0_value_value = /*ap*/
      ctx2[25].ssid)) {
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
  let t0;
  let div1;
  let span0;
  let t1_value = (
    /*ap*/
    (ctx[25].ssid ? (
      /*ap*/
      ctx[25].ssid
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
    ctx[25].frequency + ""
  );
  let t4;
  let t5;
  let div4;
  let button;
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
      ctx[11](
        /*ap*/
        ctx[25]
      )
    );
  }
  function click_handler_2() {
    return (
      /*click_handler_2*/
      ctx[12](
        /*ap*/
        ctx[25]
      )
    );
  }
  function select_block_type_2(ctx2, dirty) {
    if (
      /*ap*/
      ctx2[25].state >= 1 && /*ap*/
      ctx2[25].state <= 3
    )
      return create_if_block_3;
    if (
      /*ap*/
      ctx2[25].saved
    )
      return create_if_block_7;
  }
  let current_block_type = select_block_type_2(ctx);
  let if_block0 = current_block_type && current_block_type(ctx);
  function click_handler_3() {
    return (
      /*click_handler_3*/
      ctx[13](
        /*ap*/
        ctx[25]
      )
    );
  }
  function click_handler_4() {
    return (
      /*click_handler_4*/
      ctx[14](
        /*ap*/
        ctx[25]
      )
    );
  }
  function click_handler_5() {
    return (
      /*click_handler_5*/
      ctx[15](
        /*ap*/
        ctx[25]
      )
    );
  }
  let if_block1 = (
    /*isApSelected*/
    ctx[2] && /*ap*/
    ctx[25].ssid == /*selectedAp*/
    ctx[3].ssid && create_if_block(ctx)
  );
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
      button = element("button");
      button.textContent = "Details";
      t7 = space();
      div5 = element("div");
      t8 = space();
      if (if_block1)
        if_block1.c();
      t9 = space();
      attr(img, "height", "25");
      if (!src_url_equal(img.src, img_src_value = "img/" + /*ap*/
      ctx[25].strength_icon))
        attr(img, "src", img_src_value);
      attr(div0, "class", "col-auto");
      set_style(div0, "width", "60px");
      attr(div1, "class", "col text-break");
      attr(div2, "class", div2_class_value = "col-auto " + /*ap*/
      (ctx[25].state == 2 ? "text-white" : ""));
      set_style(div2, "width", "150px");
      attr(div3, "class", "col-auto");
      set_style(div3, "width", "125px");
      attr(button, "class", "btn btn-outline-light float-right");
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
      append(div4, button);
      append(div6, t7);
      append(div6, div5);
      append(div6, t8);
      if (if_block1)
        if_block1.m(div6, null);
      append(div6, t9);
      current = true;
      if (!mounted) {
        dispose = [
          listen(div0, "click", click_handler_1),
          listen(div1, "click", click_handler_2),
          listen(div2, "click", click_handler_3),
          listen(div3, "click", click_handler_4),
          listen(button, "click", click_handler_5)
        ];
        mounted = true;
      }
    },
    p(new_ctx, dirty) {
      ctx = new_ctx;
      if (!current || dirty[0] & /*aps*/
      1 && !src_url_equal(img.src, img_src_value = "img/" + /*ap*/
      ctx[25].strength_icon)) {
        attr(img, "src", img_src_value);
      }
      if ((!current || dirty[0] & /*aps*/
      1) && t1_value !== (t1_value = /*ap*/
      (ctx[25].ssid ? (
        /*ap*/
        ctx[25].ssid
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
      1 && div2_class_value !== (div2_class_value = "col-auto " + /*ap*/
      (ctx[25].state == 2 ? "text-white" : ""))) {
        attr(div2, "class", div2_class_value);
      }
      if ((!current || dirty[0] & /*aps*/
      1) && t4_value !== (t4_value = /*ap*/
      ctx[25].frequency + ""))
        set_data(t4, t4_value);
      if (
        /*isApSelected*/
        ctx[2] && /*ap*/
        ctx[25].ssid == /*selectedAp*/
        ctx[3].ssid
      ) {
        if (if_block1) {
          if_block1.p(ctx, dirty);
          if (dirty[0] & /*isApSelected, aps, selectedAp*/
          13) {
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
function create_header_slot(ctx) {
  let div;
  return {
    c() {
      div = element("div");
      div.innerHTML = `<h2>Connected successfully</h2>`;
      attr(div, "class", "col");
      attr(div, "slot", "header");
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
function create_content_slot(ctx) {
  let div;
  return {
    c() {
      div = element("div");
      div.innerHTML = `To continue please connect to your local network<br/>
    It&#39;s possible this will happen automatically<br/>
    Once connected, this should close on its own`;
      attr(div, "class", "col");
      attr(div, "slot", "content");
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
function create_fragment(ctx) {
  let h1;
  let t1;
  let t2;
  let h20;
  let t4;
  let div7;
  let div6;
  let t13;
  let each_blocks = [];
  let each_1_lookup = /* @__PURE__ */ new Map();
  let t14;
  let h21;
  let t16;
  let div11;
  let div10;
  let div8;
  let connectform;
  let t17;
  let div9;
  let div11_transition;
  let t18;
  let div14;
  let div13;
  let div12;
  let button;
  let t20;
  let modal;
  let updating_showModal;
  let updating_dialog;
  let current;
  let mounted;
  let dispose;
  function select_block_type(ctx2, dirty) {
    if (
      /*connectionDetails*/
      ctx2[1].length
    )
      return create_if_block_8;
    return create_else_block_3;
  }
  let current_block_type = select_block_type(ctx);
  let if_block = current_block_type(ctx);
  let each_value = ensure_array_like(
    /*aps*/
    ctx[0]
  );
  const get_key = (ctx2) => (
    /*ap*/
    ctx2[25].ssid
  );
  for (let i = 0; i < each_value.length; i += 1) {
    let child_ctx = get_each_context(ctx, each_value, i);
    let key = get_key(child_ctx);
    each_1_lookup.set(key, each_blocks[i] = create_each_block(key, child_ctx));
  }
  connectform = new ConnectForm({ props: { ap: {} } });
  function modal_showModal_binding(value) {
    ctx[16](value);
  }
  function modal_dialog_binding(value) {
    ctx[17](value);
  }
  let modal_props = {
    $$slots: {
      content: [create_content_slot],
      header: [create_header_slot]
    },
    $$scope: { ctx }
  };
  if (
    /*showRedirectModal*/
    ctx[4] !== void 0
  ) {
    modal_props.showModal = /*showRedirectModal*/
    ctx[4];
  }
  if (
    /*redirectDialog*/
    ctx[5] !== void 0
  ) {
    modal_props.dialog = /*redirectDialog*/
    ctx[5];
  }
  modal = new Modal({ props: modal_props });
  binding_callbacks.push(() => bind(modal, "showModal", modal_showModal_binding));
  binding_callbacks.push(() => bind(modal, "dialog", modal_dialog_binding));
  return {
    c() {
      h1 = element("h1");
      h1.textContent = "Wi-Fi Setup";
      t1 = space();
      if_block.c();
      t2 = space();
      h20 = element("h2");
      h20.textContent = "Available networks";
      t4 = space();
      div7 = element("div");
      div6 = element("div");
      div6.innerHTML = `<div class="col-auto" style="width: 60px;"></div> <div class="col"><span>SSID</span></div> <div class="col-auto" style="width: 150px">State</div> <div class="col-auto" style="width: 125px"><span>Frequency</span></div> <div class="col-auto" style="width: 150px"></div> <div class="w-100"></div>`;
      t13 = space();
      for (let i = 0; i < each_blocks.length; i += 1) {
        each_blocks[i].c();
      }
      t14 = space();
      h21 = element("h2");
      h21.textContent = "Connect to another network";
      t16 = space();
      div11 = element("div");
      div10 = element("div");
      div8 = element("div");
      create_component(connectform.$$.fragment);
      t17 = space();
      div9 = element("div");
      div9.innerHTML = ``;
      t18 = space();
      div14 = element("div");
      div13 = element("div");
      div12 = element("div");
      button = element("button");
      button.innerHTML = `Back to wizard <img src="img/arrow-left.svg" height="16" alt="back arrow"/>`;
      t20 = space();
      create_component(modal.$$.fragment);
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
      insert(target, h1, anchor);
      insert(target, t1, anchor);
      if_block.m(target, anchor);
      insert(target, t2, anchor);
      insert(target, h20, anchor);
      insert(target, t4, anchor);
      insert(target, div7, anchor);
      append(div7, div6);
      append(div7, t13);
      for (let i = 0; i < each_blocks.length; i += 1) {
        if (each_blocks[i]) {
          each_blocks[i].m(div7, null);
        }
      }
      insert(target, t14, anchor);
      insert(target, h21, anchor);
      insert(target, t16, anchor);
      insert(target, div11, anchor);
      append(div11, div10);
      append(div10, div8);
      mount_component(connectform, div8, null);
      append(div10, t17);
      append(div10, div9);
      insert(target, t18, anchor);
      insert(target, div14, anchor);
      append(div14, div13);
      append(div13, div12);
      append(div12, button);
      insert(target, t20, anchor);
      mount_component(modal, target, anchor);
      current = true;
      if (!mounted) {
        dispose = listen(
          button,
          "click",
          /*backToWizard*/
          ctx[8]
        );
        mounted = true;
      }
    },
    p(ctx2, dirty) {
      if (current_block_type === (current_block_type = select_block_type(ctx2)) && if_block) {
        if_block.p(ctx2, dirty);
      } else {
        if_block.d(1);
        if_block = current_block_type(ctx2);
        if (if_block) {
          if_block.c();
          if_block.m(t2.parentNode, t2);
        }
      }
      if (dirty[0] & /*aps, selectedAp, isApSelected, selectAp*/
      141) {
        each_value = ensure_array_like(
          /*aps*/
          ctx2[0]
        );
        group_outros();
        each_blocks = update_keyed_each(each_blocks, dirty, get_key, 1, ctx2, each_value, each_1_lookup, div7, outro_and_destroy_block, create_each_block, null, get_each_context);
        check_outros();
      }
      const modal_changes = {};
      if (dirty[1] & /*$$scope*/
      2) {
        modal_changes.$$scope = { dirty, ctx: ctx2 };
      }
      if (!updating_showModal && dirty[0] & /*showRedirectModal*/
      16) {
        updating_showModal = true;
        modal_changes.showModal = /*showRedirectModal*/
        ctx2[4];
        add_flush_callback(() => updating_showModal = false);
      }
      if (!updating_dialog && dirty[0] & /*redirectDialog*/
      32) {
        updating_dialog = true;
        modal_changes.dialog = /*redirectDialog*/
        ctx2[5];
        add_flush_callback(() => updating_dialog = false);
      }
      modal.$set(modal_changes);
    },
    i(local) {
      if (current)
        return;
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
      transition_in(modal.$$.fragment, local);
      current = true;
    },
    o(local) {
      for (let i = 0; i < each_blocks.length; i += 1) {
        transition_out(each_blocks[i]);
      }
      transition_out(connectform.$$.fragment, local);
      if (local) {
        if (!div11_transition)
          div11_transition = create_bidirectional_transition(div11, slide, {}, false);
        div11_transition.run(0);
      }
      transition_out(modal.$$.fragment, local);
      current = false;
    },
    d(detaching) {
      if (detaching) {
        detach(h1);
        detach(t1);
        detach(t2);
        detach(h20);
        detach(t4);
        detach(div7);
        detach(t14);
        detach(h21);
        detach(t16);
        detach(div11);
        detach(t18);
        detach(div14);
        detach(t20);
      }
      if_block.d(detaching);
      for (let i = 0; i < each_blocks.length; i += 1) {
        each_blocks[i].d();
      }
      destroy_component(connectform);
      if (detaching && div11_transition)
        div11_transition.end();
      destroy_component(modal, detaching);
      mounted = false;
      dispose();
    }
  };
}
const AP_FETCH_INTERVAL = 3e3;
const INFO_FETCH_INTERVAL = 1e3;
const PROBE_INTERVAL = 2e3;
function changeHost(probeUrl) {
  window.location.href = probeUrl + "/wifi";
}
async function probe(url) {
  try {
    const response = await fetch(url + "/wifi/api/probe", { method: "HEAD" });
    if (response.status != 200) {
      return false;
    }
    return true;
  } catch {
    return false;
  }
}
function instance($$self, $$props, $$invalidate) {
  let aps = [];
  let probeDetails = [];
  let connectionDetails = [];
  let isApSelected = false;
  let selectedAp = {};
  let autoRedirect = new AutoRedirect(changeHost);
  let showRedirectModal = false;
  let redirectDialog;
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
    let newConnectionDetails = info.connection_details;
    let newIps = [];
    newConnectionDetails.forEach((detail) => {
      newIps.push(detail.ip);
      let activeConnection = info.active_connections.find((ac) => ac.interface == detail.interface);
      if (activeConnection) {
        detail.ssid = activeConnection.ssid;
      }
    });
    if (newConnectionDetails.length > 0 && connectionDetails.length == 0 && info["hotspot_on"]) {
      autoRedirect.activate();
      $$invalidate(4, showRedirectModal = true);
    }
    if (autoRedirect.active && info["hotspot_on"] == false) {
      autoRedirect.disable();
      $$invalidate(4, showRedirectModal = false);
    }
    updateProbeDetails(newIps);
    $$invalidate(1, connectionDetails = newConnectionDetails);
  }
  function updateProbeDetails(receivedIps) {
    const old = new Set(probeDetails.map((e) => e.ip));
    const current = new Set(receivedIps);
    const toRemove = new Set([...old].filter((x) => !current.has(x)));
    const toAdd = new Set([...current].filter((x) => !old.has(x)));
    for (const ipToRemove of toRemove) {
      probeDetails.splice(probeDetails.indexOf((e) => e.ip == ipToRemove), 1);
    }
    for (const ipToAdd of toAdd) {
      const port = window.location.port;
      let detail = {
        ip: ipToAdd,
        url: requestUrl.replace(window.location.host, `${ipToAdd}:${port}`),
        sameAsHost: window.location.host == `${ipToAdd}:${port}`,
        reachable: void 0
      };
      probeDetails.push(detail);
    }
    if (toAdd.size) {
      probeAll();
    }
  }
  async function probeAll() {
    for (const probeDetail of probeDetails) {
      probeDetail.reachable = await probe(probeDetail.url);
    }
    autoRedirect.lookForRedirect(probeDetails);
  }
  async function fetchWifiList() {
    try {
      const response = await fetch(requestUrl + "/wifi/api/ap_list");
      const data = await response.json();
      processAps(data.aps);
    } catch (error) {
      console.log(error);
    }
  }
  async function fetchConnectionInfo() {
    fetch(requestUrl + "/wifi/api/connection_info").then((response) => response.json()).then((data) => {
      processConnectionInfo(data);
    }).catch((error) => {
      console.log(error);
    });
  }
  function selectAp(ap) {
    if (selectedAp.ssid == ap.ssid) {
      return;
    }
    $$invalidate(2, isApSelected = true);
    $$invalidate(3, selectedAp = ap);
  }
  function backToWizard() {
    window.location.href = requestUrl + "/wizard";
  }
  onMount(() => {
    const apInterval = setInterval(fetchWifiList, AP_FETCH_INTERVAL);
    const infoInterval = setInterval(fetchConnectionInfo, INFO_FETCH_INTERVAL);
    const probeInterval = setInterval(probeAll, PROBE_INTERVAL);
    fetchWifiList();
    fetchConnectionInfo();
    return () => {
      clearInterval(apInterval);
      clearInterval(infoInterval);
      clearInterval(probeInterval);
    };
  });
  const click_handler = (probeDetail) => changeHost(probeDetail.url);
  const func = (connectionDetail, e) => e.ip == connectionDetail.ip;
  const click_handler_1 = (ap) => {
    selectAp(ap);
  };
  const click_handler_2 = (ap) => {
    selectAp(ap);
  };
  const click_handler_3 = (ap) => {
    selectAp(ap);
  };
  const click_handler_4 = (ap) => {
    selectAp(ap);
  };
  const click_handler_5 = (ap) => {
    selectAp(ap);
  };
  function modal_showModal_binding(value) {
    showRedirectModal = value;
    $$invalidate(4, showRedirectModal);
  }
  function modal_dialog_binding(value) {
    redirectDialog = value;
    $$invalidate(5, redirectDialog);
  }
  return [
    aps,
    connectionDetails,
    isApSelected,
    selectedAp,
    showRedirectModal,
    redirectDialog,
    probeDetails,
    selectAp,
    backToWizard,
    click_handler,
    func,
    click_handler_1,
    click_handler_2,
    click_handler_3,
    click_handler_4,
    click_handler_5,
    modal_showModal_binding,
    modal_dialog_binding
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
