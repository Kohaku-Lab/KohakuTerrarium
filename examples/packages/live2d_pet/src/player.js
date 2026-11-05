import { CubismFramework } from "../vendor/Framework/src/live2dcubismframework";
import { CubismModelSettingJson } from "../vendor/Framework/src/cubismmodelsettingjson";
import { CubismUserModel } from "../vendor/Framework/src/model/cubismusermodel";
import { CubismMatrix44 } from "../vendor/Framework/src/math/cubismmatrix44";
import { CubismEyeBlink } from "../vendor/Framework/src/effect/cubismeyeblink";
import {
  CubismBreath,
  BreathParameterData,
} from "../vendor/Framework/src/effect/cubismbreath";
import { CubismShaderManager_WebGL } from "../vendor/Framework/src/rendering/cubismshader_webgl";
import { FrameStats } from "./performance.js";
import { WorkActions } from "./work-actions.js";
import { ActionPose, validateCapabilities } from "./action-pose.js";
import { loadGestureMotions } from "./gesture-motions.js";
import { modelAdapter, toggleExpression } from "../ui/models/index.js";
import { hitRegion, applyReaction } from "./touch.js";
import { SpeechMouth } from "../ui/speech.js";

export function initializeFramework() {
  if (!CubismFramework.isStarted()) {
    CubismFramework.startUp();
    CubismFramework.initialize();
  }
}

export class DragonModel extends CubismUserModel {
  constructor(profile = "dragon") {
    super();
    this.profile = profile;
    this.adapter = modelAdapter(profile);
    this.workActions = new WorkActions(profile);
    this.actionPose = new ActionPose(this);
    this.motions = new Map();
    this.expressions = new Map();
    this.expressionDefinitions = new Map();
    this.appearanceParameters = new Set();
    this.activeExpressions = new Set();
    this.currentMotion = "Idle";
  }

  createRenderer(width, height) {
    const groups = new Set(
      this._model
        .getDrawableMasks()
        .filter((mask) => mask.length > 0)
        .map((mask) => [...mask].sort((a, b) => a - b).join(",")),
    );
    // Cubism Web supports 36 unique masks in one target, or 32 per target
    // with multiple targets. Overflow otherwise overlaps unrelated masks.
    const buffers = groups.size <= 36 ? 1 : Math.ceil(groups.size / 32);
    super.createRenderer(width, height, buffers);
    if (buffers > 1) this.getRenderer().setClippingMaskBufferSize(1024);
  }

  validateActionSet(catalog) {
    validateCapabilities(catalog, this);
  }

  loadMotion(...args) {
    const motion = super.loadMotion(...args);
    motion?.setEffectIds(
      ["ParamEyeLOpen", "ParamEyeROpen"].map((name) =>
        CubismFramework.getIdManager().getId(name),
      ),
      [],
    );
    return motion;
  }

  initializeEffects(setting) {
    loadGestureMotions(this);
    const id = (name) => CubismFramework.getIdManager().getId(name);
    this._eyeBlink = CubismEyeBlink.create(setting);
    if (!setting)
      this._eyeBlink.setParameterIds([
        id("ParamEyeLOpen"),
        id("ParamEyeROpen"),
      ]);
    this._breath = CubismBreath.create();
    this._breath.setParameters([
      new BreathParameterData(id("ParamBreath"), 0.5, 0.5, 3.2, 1),
    ]);
  }

  loadExpression(buffer, size, name) {
    const definition = JSON.parse(new TextDecoder().decode(buffer));
    this.expressionDefinitions.set(name, definition);
    if (this.adapter.appearanceStates?.includes(name)) {
      const ids = this._model.getModel().parameters.ids;
      for (const { Id } of definition.Parameters) {
        const index = ids.indexOf(Id);
        if (index >= 0) this.appearanceParameters.add(index);
      }
    }
    return super.loadExpression(buffer, size, name);
  }

  motion(name) {
    const motion = this.motions.get(name);
    if (!motion) throw new Error(`Unknown motion: ${name}`);
    this._motionManager.stopAllMotions();
    this._model.loadParameters();
    for (let i = 0; i < this._model.getParameterCount(); i++) {
      this._model.setParameterValueByIndex(
        i,
        this._model.getParameterDefaultValue(i),
      );
    }
    this._model.saveParameters();
    this.currentMotion = name;
    this._motionManager.startMotionPriority(motion, false, 3);
  }

  expression(name) {
    this.workActions.refreshBounds();
    if (!name) {
      this._expressionManager.stopAllMotions();
      this.activeExpressions.clear();
      return [];
    }
    if (!this.expressionDefinitions.has(name))
      throw new Error(`Unknown expression: ${name}`);
    const active = new Set(
      toggleExpression(this.adapter, this.activeExpressions, name),
    );
    this.activeExpressions = active;
    const parameters = [...active].flatMap(
      (key) => this.expressionDefinitions.get(key).Parameters,
    );
    const buffer = new TextEncoder().encode(
      JSON.stringify({
        Type: "Live2D Expression",
        FadeInTime: 0.4,
        FadeOutTime: 0.4,
        Parameters: parameters,
      }),
    ).buffer;
    this._expressionManager.startMotion(
      super.loadExpression(buffer, buffer.byteLength, "combined"),
      true,
    );
    return [...active];
  }

  update(dt, gaze, physics, reaction, action, mouth = null) {
    const model = this._model;
    model.loadParameters();
    this._motionManager.updateMotion(model, dt);
    for (const index of this.appearanceParameters)
      model.setParameterValueByIndex(
        index,
        model.getParameterDefaultValue(index),
      );
    model.saveParameters();
    this._expressionManager.updateMotion(model, dt);
    const appearance = [...this.appearanceParameters].map((index) => [
      index,
      model.getParameterValueByIndex(index),
    ]);
    if (this.currentMotion !== "Sleep") {
      this._eyeBlink.updateParameters(model, dt);
      this._dragManager.set(gaze.x, gaze.y);
      this._dragManager.update(dt);
      const x = this._dragManager.getX();
      const y = this._dragManager.getY();
      for (const [name, value] of [
        ["ParamAngleX", x * 20],
        ["ParamAngleY", y * 15],
        ["ParamBodyAngleX", x * 6],
        ["ParamEyeBallX", x],
        ["ParamEyeBallY", y],
      ]) {
        model.addParameterValueById(
          CubismFramework.getIdManager().getId(name),
          value,
        );
      }
    }
    this._breath.updateParameters(model, dt);
    this.workActions.apply(model, dt, action, this.currentMotion === "Sleep");
    if (physics) this._physics?.evaluate(model, dt);
    else if (this.currentMotion !== "Sleep") {
      for (const [name, alias] of Object.entries(this.adapter.aliases)) {
        const id = CubismFramework.getIdManager();
        model.setParameterValueById(
          id.getId(alias),
          model.getParameterValueById(id.getId(name)),
        );
      }
    }
    if (this.currentMotion !== "Sleep") {
      const protectedParameters = new Set(
        [...this.activeExpressions].flatMap((name) =>
          this.expressionDefinitions
            .get(name)
            .Parameters.map((parameter) => parameter.Id),
        ),
      );
      applyReaction(model, reaction, protectedParameters, this.adapter.aliases);
    }
    this.actionPose.apply(model, dt, action, this.currentMotion === "Sleep");
    this.applySpeech(this.currentMotion === "Sleep" ? null : mouth);
    for (const [index, value] of appearance)
      model.setParameterValueByIndex(index, value);
    model.update();
  }

  applySpeech(value) {
    const model = this.getModel();
    const id = CubismFramework.getIdManager().getId(this.adapter.mouth);
    this.mouthBase = value == null ? null : model.getParameterValueById(id);
    if (value != null)
      model.setParameterValueById(id, Math.max(0, Math.min(1, value)));
  }

  clearSpeech() {
    if (this.mouthBase == null) return;
    const model = this.getModel();
    model.setParameterValueById(
      CubismFramework.getIdManager().getId(this.adapter.mouth),
      this.mouthBase,
    );
    this.mouthBase = null;
    model.update();
  }

  release() {
    this.workActions.release();
    this._motionManager?.stopAllMotions();
    this._expressionManager?.stopAllMotions();
    for (const motion of this.motions.values()) motion.release();
    for (const expression of this.expressions.values()) expression.release();
    this.motions.clear();
    this.expressions.clear();
    this.expressionDefinitions.clear();
    this.appearanceParameters.clear();
    this.activeExpressions.clear();
    super.release();
  }
}

export class DragonPlayer {
  hitRegion(x, y) {
    return hitRegion(this, x, y);
  }
  constructor(canvas, report) {
    this.canvas = canvas;
    this.report = report;
    this.abort = new AbortController();
    this.textures = [];
    this.gaze = { x: 0, y: 0 };
    this.physics = true;
    this.tracking = true;
    this.paused = false;
    this.speech = new SpeechMouth();
    this.zoom = 1;
    this.pan = { x: 0, y: 0 };
    this.frames = 0;
    this.dead = false;
    this.frameStats = new FrameStats();
    this.diagnostics = false;
    this.mouthRange = [1, 0];
  }

  async bytes(url) {
    if (this.resourceBytes) return this.resourceBytes(url, this.abort.signal);
    const response = await fetch(url, { signal: this.abort.signal });
    if (!response.ok)
      throw new Error(
        `资源读取失败 (${response.status}): ${new URL(url).pathname.split("/").pop()}`,
      );
    return response.arrayBuffer();
  }

  async load(manifestUrl, shaderUrl) {
    initializeFramework();
    this.gl = this.canvas.getContext("webgl", {
      alpha: true,
      premultipliedAlpha: true,
      antialias: true,
    });
    if (!this.gl)
      throw new Error("当前窗口无法创建 WebGL，请尝试在浏览器打开预览。");
    this.report("读取原始模型…");
    const buffer = await this.bytes(manifestUrl);
    const manifest = JSON.parse(new TextDecoder().decode(buffer));
    const setting = new CubismModelSettingJson(buffer, buffer.byteLength);
    this.setting = setting;
    const asset = (path) => new URL(path, manifestUrl).href;
    this.textureBytes = manifest.Demo?.textureBytes;
    this.targetFps = modelAdapter(manifest.Demo?.profile).fps;
    const model = new DragonModel(manifest.Demo?.profile);
    this.model = model;
    const moc = await this.bytes(asset(manifest.FileReferences.Moc));
    model.loadModel(moc, true);
    if (!model.getModel())
      throw new Error("模型未通过 Cubism 加载或完整性检查。");
    model.initializeEffects(setting);
    if (manifest.FileReferences.Physics) {
      const physics = await this.bytes(asset(manifest.FileReferences.Physics));
      model.loadPhysics(physics, physics.byteLength);
    }
    for (const [name, motions] of Object.entries(
      manifest.FileReferences.Motions,
    )) {
      const data = await this.bytes(asset(motions[0].File));
      const motion = model.loadMotion(
        data,
        data.byteLength,
        name,
        undefined,
        undefined,
        setting,
        name,
        0,
      );
      if (!motion) throw new Error(`无法加载动作: ${name}`);
      motion.setLoop(true);
      model.motions.set(name, motion);
      model.workActions.load(model, name, data);
    }
    for (const expression of manifest.FileReferences.Expressions) {
      const data = await this.bytes(asset(expression.File));
      const motion = model.loadExpression(
        data,
        data.byteLength,
        expression.Name,
      );
      if (!motion) throw new Error(`无法加载表情: ${expression.Name}`);
      model.expressions.set(expression.Name, motion);
    }
    for (const [name, path] of Object.entries(model.adapter.workMotions))
      model.workActions.load(model, name, await this.bytes(asset(path)));
    this.resize();
    model.createRenderer(this.canvas.width, this.canvas.height);
    const renderer = model.getRenderer();
    renderer.startUp(this.gl);
    renderer.setIsPremultipliedAlpha(true);
    this.shaderUrl = shaderUrl;
    this.report(`准备 ${manifest.FileReferences.Textures.length} 张纹理…`);
    for (const [index, path] of manifest.FileReferences.Textures.entries()) {
      const bytes = await this.bytes(asset(path));
      const blobUrl = URL.createObjectURL(
        new Blob([bytes], {
          type: path.toLowerCase().endsWith(".webp")
            ? "image/webp"
            : "image/png",
        }),
      );
      try {
        const img = new Image();
        img.src = blobUrl;
        await img.decode();
        this.abort.signal.throwIfAborted();
        const gl = this.gl;
        if (
          Math.max(img.width, img.height) > gl.getParameter(gl.MAX_TEXTURE_SIZE)
        )
          throw new Error("纹理超过当前设备支持的尺寸，请选择日常档。");
        const texture = gl.createTexture();
        this.textures.push(texture);
        gl.bindTexture(gl.TEXTURE_2D, texture);
        gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, true);
        gl.texImage2D(
          gl.TEXTURE_2D,
          0,
          gl.RGBA,
          gl.RGBA,
          gl.UNSIGNED_BYTE,
          img,
        );
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
        gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
        renderer.bindTexture(index, texture);
      } finally {
        URL.revokeObjectURL(blobUrl);
      }
    }
    model.getModel().update();
    this.bounds = this.measure();
    model.motion("Idle");
    renderer.loadShaders(shaderUrl);
    this.report("准备画面…");
    this.started = performance.now();
    this.last = this.started;
    this.fpsTime = this.started;
    this.observer = new ResizeObserver(() => this.resize());
    this.observer.observe(this.canvas);
    this.raf = requestAnimationFrame((time) => this.tick(time));
  }

  measure() {
    const model = this.model.getModel();
    const bounds = {
      left: Infinity,
      right: -Infinity,
      bottom: Infinity,
      top: -Infinity,
    };
    const parameter = this.model.adapter.boundsParameter;
    const wing = CubismFramework.getIdManager().getId(parameter || "");
    const index = model.getParameterIndex(wing);
    const original = model.getParameterValueById(wing);
    const values = parameter
      ? [
          original,
          model.getParameterMinimumValue(index),
          model.getParameterMaximumValue(index),
        ]
      : [original];
    for (const value of values) {
      if (parameter) model.setParameterValueById(wing, value);
      model.update();
      for (let i = 0; i < model.getDrawableCount(); i++) {
        if (model.getDrawableOpacity(i) <= 0) continue;
        const vertices = model.getDrawableVertices(i);
        for (let j = 0; j < vertices.length; j += 2) {
          bounds.left = Math.min(bounds.left, vertices[j]);
          bounds.right = Math.max(bounds.right, vertices[j]);
          bounds.bottom = Math.min(bounds.bottom, vertices[j + 1]);
          bounds.top = Math.max(bounds.top, vertices[j + 1]);
        }
      }
    }
    if (parameter) model.setParameterValueById(wing, original);
    model.update();
    if (!Number.isFinite(bounds.left))
      throw new Error("模型没有可显示的网格。");
    return bounds;
  }

  resize() {
    const rect = this.canvas.getBoundingClientRect();
    const ratio = Math.min(devicePixelRatio || 1, 2);
    this.canvas.width = Math.max(1, Math.round(rect.width * ratio));
    this.canvas.height = Math.max(1, Math.round(rect.height * ratio));
  }

  tick(time) {
    if (this.dead) return;
    if (this.targetFps === 30 && time - this.last < 1000 / 30 - 0.5) {
      this.raf = requestAnimationFrame((next) => this.tick(next));
      return;
    }
    try {
      const dt = Math.min((time - this.last) / 1000, 0.05);
      this.last = time;
      this.speech.suspend(
        document.hidden || this.paused || this.model.currentMotion === "Sleep",
      );
      const shader = CubismShaderManager_WebGL.getInstance().getShader(this.gl);
      if (!shader._isShaderLoaded) {
        if (time - this.started > 20000)
          throw new Error("着色器准备超时，请重新载入。");
      } else if (!document.hidden) {
        const started = this.diagnostics ? performance.now() : 0;
        if (!this.paused)
          this.model.update(
            dt,
            this.tracking ? this.gaze : { x: 0, y: 0 },
            this.physics,
            this.behavior?.sample(time),
            this.behavior?.action(time),
            this.speech.sample(),
          );
        else this.model.clearSpeech();
        this.draw();
        if (this.diagnostics) {
          this.recordFrame(time, performance.now() - started);
        } else this.frameStats.reset();
        this.frames++;
        if (time - this.fpsTime >= 1000) {
          this.report("已就绪", {
            stats: this.diagnostics ? this.frameStats.summary() : null,
            fps: Math.round((this.frames * 1000) / (time - this.fpsTime)),
            drawables: this.model.getModel().getDrawableCount(),
            mouthRange: this.mouthRange.map((value) => Math.round(value * 100)),
          });
          this.mouthRange = [1, 0];
          this.frames = 0;
          this.fpsTime = time;
        }
      }
      this.raf = requestAnimationFrame((next) => this.tick(next));
    } catch (error) {
      this.report(error.message, { error: true });
      this.dispose();
    }
  }

  recordFrame(time, cost) {
    this.frameStats.add(time, cost);
    const mouth = this.model
      .getModel()
      .getParameterValueById(
        CubismFramework.getIdManager().getId(this.model.adapter.mouth),
      );
    this.mouthRange[0] = Math.min(this.mouthRange[0], mouth);
    this.mouthRange[1] = Math.max(this.mouthRange[1], mouth);
  }

  draw() {
    const gl = this.gl;
    const b = this.bounds;
    const aspect = this.canvas.width / this.canvas.height;
    const scale =
      Math.min(1.8 / (b.top - b.bottom), (1.8 * aspect) / (b.right - b.left)) *
      this.zoom;
    const matrix = new CubismMatrix44();
    matrix.scale(scale / aspect, scale);
    matrix.translate(
      ((-(b.left + b.right) / 2) * scale) / aspect + this.pan.x,
      (-(b.top + b.bottom) / 2) * scale + this.pan.y,
    );
    gl.viewport(0, 0, this.canvas.width, this.canvas.height);
    gl.clearColor(0, 0, 0, 0);
    gl.clear(gl.COLOR_BUFFER_BIT);
    const renderer = this.model.getRenderer();
    renderer.setMvpMatrix(matrix);
    renderer.setRenderState(null, [
      0,
      0,
      this.canvas.width,
      this.canvas.height,
    ]);
    renderer.drawModel(this.shaderUrl);
    this.afterDraw?.();
  }

  dispose() {
    if (this.dead) return;
    this.dead = true;
    this.speech.reset();
    this.abort.abort();
    cancelAnimationFrame(this.raf);
    this.observer?.disconnect();
    this.model?.release();
    this.setting?.release();
    for (const texture of this.textures) this.gl.deleteTexture(texture);
    this.textures.length = 0;
    this.gl?.getExtension("WEBGL_lose_context")?.loseContext();
  }
}
