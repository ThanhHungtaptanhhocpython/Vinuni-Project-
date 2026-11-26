"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import type { ReplayServoSample } from "@/lib/api/run-replay";
import { loadMjcfModel, type MjcfBody, type MjcfModel } from "@/lib/robot/mjcf";
import { loadStlGeometry } from "@/lib/robot/mesh-cache";

type ArmProfileId = "franka" | "so101";

interface RobotSource {
  xmlUrl: string;
  label: string;
}

const ROBOT_SOURCES: Record<ArmProfileId, RobotSource> = {
  franka: { xmlUrl: "/robots/franka/panda.xml", label: "Franka Panda" },
  so101: { xmlUrl: "/robots/so101/so101.xml", label: "SO-101" },
};

const POSE_ANIMATION_MS = 850;

interface BuiltJoint {
  group: THREE.Group;
  axis: THREE.Vector3;
  range?: [number, number];
}

interface BuiltScene {
  root: THREE.Group;
  joints: Map<string, BuiltJoint>;
  jointOrder: string[];
}

interface Tween {
  from: Map<string, number>;
  to: Map<string, number>;
  startedAt: number;
}

interface ViewerContext {
  root: THREE.Group;
  joints: Map<string, BuiltJoint>;
  jointOrder: string[];
  current: Map<string, number>;
  tween: Tween | null;
  tweenKey: string | null;
  framed: boolean;
  reframe: () => void;
}

function profileFrom(robotModel?: string | null, sample?: ReplayServoSample | null): ArmProfileId {
  const model = robotModel?.toLowerCase() ?? "";
  if (model.includes("franka")) return "franka";
  if (model.includes("so101") || model.includes("so-101") || model.includes("so_101")) return "so101";
  const jointCount = sample?.joints.length ?? 0;
  return jointCount >= 7 ? "franka" : "so101";
}

function quatFromMjcf([w, x, y, z]: [number, number, number, number]): THREE.Quaternion {
  return new THREE.Quaternion(x, y, z, w).normalize();
}

async function buildBody(body: MjcfBody, parent: THREE.Group, joints: Map<string, BuiltJoint>): Promise<void> {
  const frame = new THREE.Group();
  frame.position.set(...body.pos);
  frame.quaternion.copy(quatFromMjcf(body.quat));
  parent.add(frame);

  let content: THREE.Group = frame;
  if (body.joint) {
    const pivot = new THREE.Group();
    pivot.position.set(...body.joint.pos);
    frame.add(pivot);
    const inner = new THREE.Group();
    inner.position.set(-body.joint.pos[0], -body.joint.pos[1], -body.joint.pos[2]);
    pivot.add(inner);
    joints.set(body.joint.name, {
      group: pivot,
      axis: new THREE.Vector3(...body.joint.axis).normalize(),
      range: body.joint.range,
    });
    content = inner;
  }

  await Promise.all([
    ...body.geoms.map(async (geom) => {
      const geometry = await loadStlGeometry(geom.meshUrl);
      const material = new THREE.MeshStandardMaterial({
        color: new THREE.Color(geom.rgba[0], geom.rgba[1], geom.rgba[2]),
        roughness: 0.55,
        metalness: 0.15,
      });
      const mesh = new THREE.Mesh(geometry, material);
      mesh.position.set(...geom.pos);
      mesh.quaternion.copy(quatFromMjcf(geom.quat));
      content.add(mesh);
    }),
    ...body.children.map((child) => buildBody(child, content, joints)),
  ]);
}

async function buildScene(model: MjcfModel): Promise<BuiltScene> {
  const root = new THREE.Group();
  const joints = new Map<string, BuiltJoint>();
  await buildBody(model.root, root, joints);
  return { root, joints, jointOrder: model.jointOrder };
}

function normalizeJointName(name: string): string {
  /* Edge sim streams `joint_0`…`joint_6`; MJCF uses `joint1`…`joint7`. */
  const indexed = /^joint_(\d+)$/.exec(name);
  if (indexed) return `joint${Number(indexed[1]) + 1}`;
  return name;
}

function jointAngles(sample: ReplayServoSample, jointOrder: string[]): Map<string, number> {
  const byName = new Map(
    sample.joints.map((joint) => [normalizeJointName(joint.name), joint.pos ?? 0]),
  );
  const angles = new Map<string, number>();
  jointOrder.forEach((name, index) => {
    if (byName.has(name)) {
      angles.set(name, byName.get(name) ?? 0);
    } else if (sample.joints[index]) {
      angles.set(name, sample.joints[index].pos ?? 0);
    }
  });
  return angles;
}

function applyAngles(joints: Map<string, BuiltJoint>, angles: Map<string, number>): void {
  angles.forEach((angle, name) => {
    const joint = joints.get(name);
    if (!joint) return;
    const clamped = joint.range ? Math.min(joint.range[1], Math.max(joint.range[0], angle)) : angle;
    joint.group.quaternion.setFromAxisAngle(joint.axis, clamped);
  });
}

function frameArmInView(
  root: THREE.Group,
  camera: THREE.PerspectiveCamera,
  controls: OrbitControls,
): void {
  /* Clear any previous centering offset before measuring. */
  root.position.set(0, 0, 0);
  root.updateMatrixWorld(true);
  const box = new THREE.Box3().setFromObject(root);
  if (box.isEmpty()) return;

  const center = box.getCenter(new THREE.Vector3());
  root.position.set(-center.x, -center.y, -center.z);
  root.updateMatrixWorld(true);

  const size = new THREE.Box3().setFromObject(root).getSize(new THREE.Vector3());
  const maxDim = Math.max(size.x, size.y, size.z, 0.15);
  const fov = THREE.MathUtils.degToRad(camera.fov);
  const fitHeight = maxDim / (2 * Math.tan(fov / 2));
  const fitWidth = fitHeight / Math.max(camera.aspect, 0.2);
  const distance = Math.max(fitHeight, fitWidth) * 1.55;

  camera.near = Math.max(distance / 200, 0.01);
  camera.far = Math.max(distance * 40, 20);
  camera.updateProjectionMatrix();

  camera.position.set(distance * 0.7, distance * 0.5, distance * 0.85);
  controls.target.set(0, 0, 0);
  controls.minDistance = distance * 0.3;
  controls.maxDistance = distance * 5;
  controls.minPolarAngle = 0.2;
  controls.maxPolarAngle = Math.PI * 0.48;
  controls.update();
}

function sampleKey(sample: ReplayServoSample): string {
  return `${sample.sequence}:${sample.joints.map((joint) => `${joint.name}:${joint.pos ?? 0}`).join("|")}`;
}

export function RobotArmViewer({
  sample,
  robotModel,
}: {
  sample?: ReplayServoSample | null;
  robotModel?: string | null;
}) {
  const mountRef = useRef<HTMLDivElement | null>(null);
  const ctxRef = useRef<ViewerContext | null>(null);
  const profile = useMemo(() => profileFrom(robotModel, sample), [robotModel, sample]);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const source = ROBOT_SOURCES[profile];

  useEffect(() => {
    const mount = mountRef.current;
    if (!mount) return;
    let disposed = false;
    setStatus("loading");
    ctxRef.current = null;

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
    renderer.setClearColor(0x0a0a0b, 1);
    renderer.setPixelRatio(Math.min(2, window.devicePixelRatio || 1));
    renderer.domElement.style.display = "block";
    renderer.domElement.style.width = "100%";
    renderer.domElement.style.height = "100%";
    mount.appendChild(renderer.domElement);

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(40, 1, 0.01, 10);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;

    scene.add(new THREE.AmbientLight(0xffffff, 0.65));
    const keyLight = new THREE.DirectionalLight(0xffffff, 0.9);
    keyLight.position.set(1, 1.4, 1.2);
    scene.add(keyLight);
    const fillLight = new THREE.DirectionalLight(0xffffff, 0.35);
    fillLight.position.set(-1.2, 0.6, -0.8);
    scene.add(fillLight);

    const resize = () => {
      const rect = mount.getBoundingClientRect();
      const width = Math.max(1, rect.width);
      const height = Math.max(1, rect.height);
      renderer.setSize(width, height, false);
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
      const ctx = ctxRef.current;
      if (ctx?.framed) ctx.reframe();
    };

    loadMjcfModel(source.xmlUrl)
      .then(buildScene)
      .then((built) => {
        if (disposed) return;
        /* MJCF is Z-up; Three.js is Y-up. */
        built.root.rotation.x = -Math.PI / 2;
        scene.add(built.root);

        const reframe = () => frameArmInView(built.root, camera, controls);
        resize();
        reframe();

        ctxRef.current = {
          root: built.root,
          joints: built.joints,
          jointOrder: built.jointOrder,
          current: new Map(),
          tween: null,
          tweenKey: null,
          framed: true,
          reframe,
        };
        setStatus("ready");
      })
      .catch((err) => {
        console.error("RobotArmViewer failed to load model", source.xmlUrl, err);
        if (!disposed) setStatus("error");
      });

    resize();
    const observer = new ResizeObserver(resize);
    observer.observe(mount);

    let frame = 0;
    const render = () => {
      const ctx = ctxRef.current;
      if (ctx?.tween) {
        const tween = ctx.tween;
        const progress = Math.min(1, (performance.now() - tween.startedAt) / POSE_ANIMATION_MS);
        const eased = 1 - Math.pow(1 - progress, 3);
        const interpolated = new Map<string, number>();
        tween.to.forEach((target, name) => {
          const start = tween.from.get(name) ?? target;
          let delta = target - start;
          if (delta > Math.PI) delta -= Math.PI * 2;
          if (delta < -Math.PI) delta += Math.PI * 2;
          interpolated.set(name, start + delta * eased);
        });
        applyAngles(ctx.joints, interpolated);
        ctx.current = interpolated;
        if (progress >= 1) ctx.tween = null;
      }
      controls.update();
      renderer.render(scene, camera);
      frame = requestAnimationFrame(render);
    };
    frame = requestAnimationFrame(render);

    return () => {
      disposed = true;
      cancelAnimationFrame(frame);
      observer.disconnect();
      controls.dispose();
      renderer.dispose();
      mount.removeChild(renderer.domElement);
      ctxRef.current = null;
    };
  }, [source.xmlUrl]);

  useEffect(() => {
    const ctx = ctxRef.current;
    if (!ctx || !sample) return;
    const nextKey = sampleKey(sample);
    if (ctx.tweenKey === nextKey) return;
    const target = jointAngles(sample, ctx.jointOrder);
    const firstPose = ctx.current.size === 0;
    if (!firstPose) {
      ctx.tween = { from: new Map(ctx.current), to: target, startedAt: performance.now() };
    } else {
      applyAngles(ctx.joints, target);
      ctx.current = target;
      ctx.tween = null;
      /* Reframe after the live pose is applied — zero-pose framing left the
         stretched Franka peeking from a corner. */
      ctx.reframe();
    }
    ctx.tweenKey = nextKey;
  }, [sample, status]);

  return (
    <div className="overflow-hidden rounded-lg border border-[var(--line)] bg-ink-950">
      <div className="flex items-center justify-between gap-3 border-b border-[var(--line)] px-3 py-2">
        <div>
          <h3 className="eyebrow">Servo arm view</h3>
          <p className="data text-[12px] text-haze-500">{source.label}</p>
        </div>
        <span className="data text-[12px] text-haze-500">
          {sample ? `${sample.joints.length} joints` : "No servo"}
        </span>
      </div>
      {/* Always mount the WebGL host. Gating it on `sample` made the load effect
          run once with mountRef=null (no sample yet) and never retry when the
          first servo frame arrived — stuck on "Đang dựng mô hình…". */}
      <div className="relative h-[360px] w-full">
        <div ref={mountRef} className="h-full w-full" aria-label={`${source.label} servo pose viewer`} />
        {!sample && (
          <div className="pointer-events-none absolute inset-0 flex items-center justify-center bg-ink-950/70 p-4 text-center text-[13.5px] text-haze-500">
            Chưa có snapshot servo để dựng cánh tay.
          </div>
        )}
        {sample && status === "loading" && (
          <div className="pointer-events-none absolute inset-0 flex items-center justify-center bg-ink-950/70 text-[13.5px] text-haze-500">
            Đang dựng mô hình 3D {source.label}…
          </div>
        )}
        {sample && status === "error" && (
          <div className="pointer-events-none absolute inset-0 flex items-center justify-center bg-ink-950/70 p-4 text-center text-[13.5px] text-haze-500">
            Không tải được mô hình 3D cho {source.label}.
          </div>
        )}
      </div>
    </div>
  );
}
