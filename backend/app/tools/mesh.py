"""Mesh approssimata da uno splat .ply (Poisson sui centri delle gaussiane, con Open3D).

Uso:  python -m app.tools.mesh <splat.ply> <mesh.ply> [--depth 10] [--scale M_PER_UNITÀ] [--up x,y,z]
                               [--model <colmap_rifinito> --dataparser <dataparser_transforms.json> --crop <splat.json>]

`ns-export poisson` non funziona con splatfacto (gli servono le normali di un NeRF). Qui si usano le gaussiane
abbastanza opache: il centro è un punto della superficie, il colore è quello base e la normale è l'asse più corto
(una gaussiana su una superficie è schiacciata contro di essa). Il verso della normale si sceglie verso la camera più
vicina, se si conoscono le camere, altrimenti verso il centro della stanza.

È un'approssimazione: pareti lisce, vetri, oggetti sottili e zone riprese poco vengono male.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

import numpy as np

from .crop import read_ply

SH_C0 = 0.28209479177387814


def quat_to_matrices(q: np.ndarray) -> np.ndarray:
    """(N,4) quaternioni w,x,y,z -> (N,3,3)."""
    q = q / np.maximum(np.linalg.norm(q, axis=1, keepdims=True), 1e-12)
    w, x, y, z = q.T
    return np.stack([
        np.stack([1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)], axis=1),
        np.stack([2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)], axis=1),
        np.stack([2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)], axis=1)], axis=1)


def surface_points(data, min_opacity: float = 0.35):
    """(punti, colori 0..1, normali non orientate) dalle gaussiane utili: opache e non gigantesche."""
    xyz = np.stack([data["x"], data["y"], data["z"]], axis=1).astype(np.float64)
    opacity = 1 / (1 + np.exp(-np.asarray(data["opacity"], dtype=np.float64)))
    scales = np.exp(np.stack([data["scale_0"], data["scale_1"], data["scale_2"]], axis=1).astype(np.float64))
    largest = scales.max(axis=1)
    keep = np.isfinite(xyz).all(axis=1) & (opacity >= min_opacity)
    if keep.any():
        keep &= largest <= np.percentile(largest[keep], 98.5)
    rgb = np.clip(0.5 + SH_C0 * np.stack([data["f_dc_0"], data["f_dc_1"], data["f_dc_2"]], axis=1), 0, 1)[keep]
    quats = np.stack([data[f"rot_{i}"] for i in range(4)], axis=1).astype(np.float64)[keep]
    rot = quat_to_matrices(quats)
    shortest = scales[keep].argmin(axis=1)
    normals = rot[np.arange(len(rot)), :, shortest]
    return xyz[keep], rgb.astype(np.float64), normals


def orient_normals(points: np.ndarray, normals: np.ndarray, viewpoints: Optional[np.ndarray]) -> np.ndarray:
    """Gira ogni normale verso chi ha visto quel punto: la camera più vicina, o il centro della stanza."""
    if viewpoints is not None and len(viewpoints):
        from scipy.spatial import cKDTree
        _d, idx = cKDTree(viewpoints).query(points)
        towards = viewpoints[idx] - points
    else:
        towards = np.median(points, axis=0) - points
    flip = np.einsum("ij,ij->i", normals, towards) < 0
    out = normals.copy()
    out[flip] *= -1
    return out


def camera_positions(model: Path, dataparser: Path, crop_info: Path) -> Optional[np.ndarray]:
    """Le posizioni delle camere nel sistema dello splat esportato: COLMAP -> Nerfstudio -> ritaglio ricentrato."""
    from ..mapdata import camera_center
    from .colmap_model import read_poses
    try:
        centers = np.array([camera_center(q, t) for q, t in read_poses(model / "images.bin").values()])
        d = json.loads(dataparser.read_text())
        t = np.array(d["transform"], dtype=np.float64)[:3, :]
        moved = (centers @ t[:, :3].T + t[:, 3]) * float(d["scale"])
        offset = np.array(json.loads(crop_info.read_text())["center"], dtype=np.float64)
        return moved - offset
    except (OSError, KeyError, ValueError, TypeError):
        return None


def build_mesh(points, colors, normals, depth: int, max_triangles: int = 600_000):
    import open3d as o3d
    from scipy.spatial import cKDTree
    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(points)
    pcd.colors = o3d.utility.Vector3dVector(colors)
    pcd.normals = o3d.utility.Vector3dVector(normals)
    _clean, kept = pcd.remove_statistical_outlier(nb_neighbors=20, std_ratio=2.0)
    pcd = pcd.select_by_index(kept)
    print(f"punti usati: {len(pcd.points)}", flush=True)
    mesh, density = o3d.geometry.TriangleMesh.create_from_point_cloud_poisson(pcd, depth=depth, linear_fit=True)
    density = np.asarray(density)
    mesh.remove_vertices_by_mask(density < np.quantile(density, 0.03))
    # Poisson chiude i buchi con "bolle" lontane dai dati: via i vertici senza punti vicini
    cloud = np.asarray(pcd.points)
    tree = cKDTree(cloud)
    spacing = np.median(tree.query(cloud[:: max(1, len(cloud) // 20000)], k=2)[0][:, 1])
    far = tree.query(np.asarray(mesh.vertices))[0] > max(spacing * 8, 1e-6)
    mesh.remove_vertices_by_mask(far)
    labels, counts, _areas = mesh.cluster_connected_triangles()
    counts = np.asarray(counts)
    if len(counts):
        mesh.remove_triangles_by_mask(counts[np.asarray(labels)] < max(200, int(counts.max() * 0.002)))
        mesh.remove_unreferenced_vertices()
    if len(mesh.triangles) > max_triangles:
        mesh = mesh.simplify_quadric_decimation(max_triangles)
    mesh.compute_vertex_normals()
    return mesh, int(len(cloud))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("splat", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--depth", type=int, default=10)
    ap.add_argument("--scale", type=float, default=None)
    ap.add_argument("--up", default=None)
    ap.add_argument("--model", type=Path)
    ap.add_argument("--dataparser", type=Path)
    ap.add_argument("--crop", type=Path)
    a = ap.parse_args(argv)
    import open3d as o3d
    print("lettura dello splat", flush=True)
    _header, data = read_ply(a.splat)
    points, colors, normals = surface_points(data)
    cameras = camera_positions(a.model, a.dataparser, a.crop) if a.model and a.dataparser and a.crop else None
    print(f"gaussiane utili: {len(points)} su {len(data)} | normali orientate verso "
          f"{'le camere' if cameras is not None else 'il centro della stanza'}", flush=True)
    if len(points) < 1000:
        print("Troppo pochi punti opachi per costruire una superficie.")
        return 1
    normals = orient_normals(points, normals, cameras)
    print("ricostruzione della superficie (Poisson)", flush=True)
    mesh, used = build_mesh(points, colors, normals, a.depth)
    if a.up:
        from .plyops import level_basis
        r = np.eye(4)
        r[:3, :3] = level_basis([float(v) for v in a.up.split(",")])
        mesh.transform(r)
    if a.scale:
        mesh.scale(a.scale, center=np.zeros(3))
    print("scrittura", flush=True)
    if not o3d.io.write_triangle_mesh(str(a.out), mesh):
        print("Scrittura della mesh fallita.")
        return 1
    info = {"vertices": int(len(mesh.vertices)), "triangles": int(len(mesh.triangles)), "points_used": used,
            "depth": a.depth, "scale_m": a.scale, "leveled": bool(a.up),
            "normals_from": "camere" if cameras is not None else "centro"}
    a.out.with_suffix(".json").write_text(json.dumps(info), encoding="utf-8")
    print(info, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
