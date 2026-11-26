/* Trình đọc MJCF tối giản: chỉ lấy đủ thông tin để dựng khung xương 3D
   (body/joint/geom mesh) cho việc hiển thị, không nhằm mô phỏng vật lý. */

export interface MjcfJoint {
  name: string;
  axis: [number, number, number];
  pos: [number, number, number];
  range?: [number, number];
}

export interface MjcfGeom {
  meshUrl: string;
  pos: [number, number, number];
  quat: [number, number, number, number];
  rgba: [number, number, number, number];
}

export interface MjcfBody {
  name: string;
  pos: [number, number, number];
  quat: [number, number, number, number];
  joint?: MjcfJoint;
  geoms: MjcfGeom[];
  children: MjcfBody[];
}

export interface MjcfModel {
  root: MjcfBody;
  jointOrder: string[];
}

function nums(value: string | null, count: number, fallback: number[]): number[] {
  if (!value) return fallback;
  const parts = value.trim().split(/\s+/).map(Number);
  return parts.length === count && parts.every(Number.isFinite) ? parts : fallback;
}

function vec3(value: string | null, fallback: [number, number, number] = [0, 0, 0]): [number, number, number] {
  return nums(value, 3, fallback) as [number, number, number];
}

function quat4(
  value: string | null,
  fallback: [number, number, number, number] = [1, 0, 0, 0],
): [number, number, number, number] {
  return nums(value, 4, fallback) as [number, number, number, number];
}

function baseName(file: string): string {
  const withoutDir = file.split("/").pop() ?? file;
  return withoutDir.replace(/\.[^.]+$/, "");
}

function isVisualGeom(el: Element): boolean {
  const type = el.getAttribute("type");
  if (type && type !== "mesh") return false;
  if (!el.getAttribute("mesh")) return false;
  const cls = el.getAttribute("class") ?? "";
  const name = el.getAttribute("name") ?? "";
  const group = el.getAttribute("group");
  if (cls.includes("collision") || name.includes("collision")) return false;
  if (group === "0" || group === "3" || group === "4") return false;
  return true;
}

async function parseMjcf(xmlUrl: string): Promise<MjcfModel> {
  const res = await fetch(xmlUrl);
  if (!res.ok) throw new Error(`Không tải được MJCF: ${xmlUrl}`);
  const text = await res.text();
  const doc = new DOMParser().parseFromString(text, "application/xml");
  if (doc.querySelector("parsererror")) throw new Error(`MJCF lỗi cú pháp: ${xmlUrl}`);

  const baseDir = xmlUrl.slice(0, xmlUrl.lastIndexOf("/") + 1);
  const meshDir = doc.querySelector("compiler")?.getAttribute("meshdir");

  const meshMap = new Map<string, string>();
  doc.querySelectorAll("asset > mesh").forEach((el) => {
    const file = el.getAttribute("file");
    if (!file) return;
    const name = el.getAttribute("name") ?? baseName(file);
    const relative = meshDir ? `${meshDir}/${file}` : file;
    meshMap.set(name, `${baseDir}${relative}`);
  });

  const materialMap = new Map<string, [number, number, number, number]>();
  doc.querySelectorAll("asset > material").forEach((el) => {
    const name = el.getAttribute("name");
    if (!name) return;
    materialMap.set(name, quat4(el.getAttribute("rgba"), [0.75, 0.75, 0.75, 1]));
  });

  const jointOrder: string[] = [];

  function readGeom(el: Element): MjcfGeom | null {
    const meshName = el.getAttribute("mesh");
    const meshUrl = meshName ? meshMap.get(meshName) : undefined;
    if (!meshUrl) return null;
    const materialName = el.getAttribute("material");
    const rgbaAttr = el.getAttribute("rgba");
    const rgba: [number, number, number, number] = rgbaAttr
      ? quat4(rgbaAttr)
      : materialName
        ? (materialMap.get(materialName) ?? [0.75, 0.75, 0.75, 1])
        : [0.75, 0.75, 0.75, 1];
    return {
      meshUrl,
      pos: vec3(el.getAttribute("pos")),
      quat: quat4(el.getAttribute("quat")),
      rgba,
    };
  }

  function readBody(el: Element): MjcfBody {
    const name = el.getAttribute("name") ?? "body";
    const jointEl = Array.from(el.children).find((c) => c.tagName === "joint");
    let joint: MjcfJoint | undefined;
    if (jointEl) {
      const jointName = jointEl.getAttribute("name") ?? name;
      jointOrder.push(jointName);
      const rangeAttr = jointEl.getAttribute("range");
      joint = {
        name: jointName,
        axis: vec3(jointEl.getAttribute("axis"), [0, 0, 1]),
        pos: vec3(jointEl.getAttribute("pos")),
        range: rangeAttr ? (nums(rangeAttr, 2, [-Math.PI, Math.PI]) as [number, number]) : undefined,
      };
    }

    const geoms: MjcfGeom[] = [];
    const children: MjcfBody[] = [];
    Array.from(el.children).forEach((child) => {
      if (child.tagName === "geom" && isVisualGeom(child)) {
        const geom = readGeom(child);
        if (geom) geoms.push(geom);
      } else if (child.tagName === "body") {
        children.push(readBody(child));
      }
    });

    return {
      name,
      pos: vec3(el.getAttribute("pos")),
      quat: quat4(el.getAttribute("quat")),
      joint,
      geoms,
      children,
    };
  }

  const worldbody = doc.querySelector("worldbody");
  const rootEl = worldbody ? Array.from(worldbody.children).find((c) => c.tagName === "body") : undefined;
  if (!rootEl) throw new Error(`MJCF không có worldbody/body: ${xmlUrl}`);

  return { root: readBody(rootEl), jointOrder };
}

const modelCache = new Map<string, Promise<MjcfModel>>();

export function loadMjcfModel(xmlUrl: string): Promise<MjcfModel> {
  let promise = modelCache.get(xmlUrl);
  if (!promise) {
    promise = parseMjcf(xmlUrl);
    modelCache.set(xmlUrl, promise);
  }
  return promise;
}
