import * as THREE from "three";
import { STLLoader } from "three/examples/jsm/loaders/STLLoader.js";
import { OBJLoader } from "three/examples/jsm/loaders/OBJLoader.js";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { mergeGeometries } from "three/examples/jsm/utils/BufferGeometryUtils.js";
import type { BufferGeometry } from "three";

const stlLoader = new STLLoader();
const objLoader = new OBJLoader();
const gltfLoader = new GLTFLoader();
const cache = new Map<string, Promise<BufferGeometry>>();

function firstGeometry(object: THREE.Object3D): BufferGeometry {
  let found: BufferGeometry | undefined;
  object.traverse((child) => {
    if (!found && child instanceof THREE.Mesh) {
      found = child.geometry as BufferGeometry;
    }
  });
  if (!found) throw new Error("OBJ không chứa mesh nào");
  return found;
}

/* Mesh mesh Franka được xuất từ .obj (text, ASCII) sang .glb (binary, có
   index) bằng assimp — giảm ~70% dung lượng tải/parse. Một .glb có thể chứa
   nhiều primitive/mesh con (mỗi material một mesh riêng khi export); gộp lại
   thành một BufferGeometry vì phía gọi chỉ cần một khối hình học duy nhất
   (màu đã lấy từ rgba khai báo trong MJCF, không phải từ vật liệu mesh). */
function allGeometries(object: THREE.Object3D): BufferGeometry[] {
  const geometries: BufferGeometry[] = [];
  object.traverse((child) => {
    if (child instanceof THREE.Mesh) {
      geometries.push(child.geometry as BufferGeometry);
    }
  });
  if (geometries.length === 0) throw new Error("GLB không chứa mesh nào");
  return geometries;
}

function loadRaw(url: string): Promise<BufferGeometry> {
  const lower = url.toLowerCase();
  if (lower.endsWith(".obj")) {
    return new Promise<BufferGeometry>((resolve, reject) => {
      objLoader.load(
        url,
        (object) => {
          try {
            resolve(firstGeometry(object));
          } catch (err) {
            reject(err);
          }
        },
        undefined,
        reject,
      );
    });
  }
  if (lower.endsWith(".glb") || lower.endsWith(".gltf")) {
    return new Promise<BufferGeometry>((resolve, reject) => {
      gltfLoader.load(
        url,
        (gltf) => {
          try {
            const geometries = allGeometries(gltf.scene);
            resolve(geometries.length === 1 ? geometries[0] : mergeGeometries(geometries, false));
          } catch (err) {
            reject(err);
          }
        },
        undefined,
        reject,
      );
    });
  }
  return new Promise<BufferGeometry>((resolve, reject) => {
    stlLoader.load(url, resolve, undefined, reject);
  });
}

export function loadStlGeometry(url: string): Promise<BufferGeometry> {
  let promise = cache.get(url);
  if (!promise) {
    promise = loadRaw(url).then((geometry) => {
      geometry.computeVertexNormals();
      return geometry;
    });
    cache.set(url, promise);
  }
  return promise;
}
