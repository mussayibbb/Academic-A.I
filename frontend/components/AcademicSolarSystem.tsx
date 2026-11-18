'use client';

import {
  ACESFilmicToneMapping,
  AdditiveBlending,
  AmbientLight,
  BackSide,
  BufferAttribute,
  BufferGeometry,
  CanvasTexture,
  Color,
  DirectionalLight,
  Group,
  Line,
  LineBasicMaterial,
  Mesh,
  MeshBasicMaterial,
  MeshPhongMaterial,
  PCFShadowMap,
  PerspectiveCamera,
  PointLight,
  Points,
  PointsMaterial,
  Scene,
  SphereGeometry,
  SRGBColorSpace,
  Vector3,
  WebGLRenderer,
} from 'three';
import { useEffect, useRef, useState } from 'react';

type AcademicSolarSystemProps = {
  totalStudents: number;
  highRisk: number;
  mediumRisk: number;
  lowRisk: number;
  reduceMotion: boolean;
};

type OrbitBody = {
  orbit: Group;
  body: Mesh;
  speed: number;
};

const RISK_COLORS = ['#e11d32', '#9299a2', '#e6e8eb'] as const;

function createRiskTexture(highRisk: number, mediumRisk: number, lowRisk: number, totalStudents: number) {
  const canvas = document.createElement('canvas');
  canvas.width = 2048;
  canvas.height = 1024;
  const context = canvas.getContext('2d');
  if (!context) throw new Error('Unable to create the risk planet surface texture.');

  const shares = totalStudents
    ? [highRisk / totalStudents, mediumRisk / totalStudents, lowRisk / totalStudents]
    : [0, 0, 0];
  context.fillStyle = '#202631';
  context.fillRect(0, 0, canvas.width, canvas.height);

  let x = 0;
  shares.forEach((share, index) => {
    const width = canvas.width * share;
    context.fillStyle = RISK_COLORS[index];
    context.fillRect(x, 0, width, canvas.height);
    x += width;
  });

  context.save();
  context.globalCompositeOperation = 'source-atop';
  const illumination = context.createLinearGradient(0, 0, 0, canvas.height);
  illumination.addColorStop(0, 'rgba(255,255,255,.25)');
  illumination.addColorStop(.3, 'rgba(255,255,255,.02)');
  illumination.addColorStop(.62, 'rgba(10,13,20,.08)');
  illumination.addColorStop(1, 'rgba(5,8,14,.55)');
  context.fillStyle = illumination;
  context.fillRect(0, 0, canvas.width, canvas.height);

  context.strokeStyle = 'rgba(255,255,255,.32)';
  context.lineWidth = 3;
  for (let longitude = 0; longitude < 8; longitude += 1) {
    const meridian = longitude * canvas.width / 8;
    context.beginPath();
    context.moveTo(meridian, 0);
    context.lineTo(meridian, canvas.height);
    context.stroke();
  }
  for (const latitude of [.25, .5, .75]) {
    const parallel = canvas.height * latitude;
    context.beginPath();
    context.moveTo(0, parallel);
    context.lineTo(canvas.width, parallel);
    context.stroke();
  }
  context.restore();

  const texture = new CanvasTexture(canvas);
  texture.colorSpace = SRGBColorSpace;
  return texture;
}

function createBodyTexture(color: string, accent: string, cratered = false) {
  const canvas = document.createElement('canvas');
  canvas.width = 512;
  canvas.height = 256;
  const context = canvas.getContext('2d');
  if (!context) throw new Error('Unable to create a planet surface texture.');

  const base = context.createLinearGradient(0, 0, 0, canvas.height);
  base.addColorStop(0, accent);
  base.addColorStop(.48, color);
  base.addColorStop(1, '#171923');
  context.fillStyle = base;
  context.fillRect(0, 0, canvas.width, canvas.height);

  for (let mark = 0; mark < 22; mark += 1) {
    const px = (mark * 137 + 43) % canvas.width;
    const py = (mark * 71 + 29) % canvas.height;
    const radius = 5 + (mark * 11) % (cratered ? 24 : 15);
    context.beginPath();
    context.ellipse(px, py, radius * 1.7, radius, (mark % 5) * .23, 0, Math.PI * 2);
    context.fillStyle = mark % 2 ? `${accent}44` : '#11151e35';
    context.fill();
    if (cratered) {
      context.beginPath();
      context.ellipse(px - radius * .18, py - radius * .16, radius * 1.18, radius * .68, (mark % 5) * .23, Math.PI, Math.PI * 2);
      context.strokeStyle = 'rgba(255,255,255,.23)';
      context.lineWidth = 2;
      context.stroke();
    }
  }

  const texture = new CanvasTexture(canvas);
  texture.colorSpace = SRGBColorSpace;
  return texture;
}

function makeOrbitPath(radiusX: number, radiusZ: number, color: string, opacity: number) {
  const points: Vector3[] = [];
  for (let point = 0; point < 192; point += 1) {
    const angle = point / 192 * Math.PI * 2;
    points.push(new Vector3(Math.cos(angle) * radiusX, 0, Math.sin(angle) * radiusZ));
  }
  const geometry = new BufferGeometry().setFromPoints(points);
  const material = new LineBasicMaterial({ color, transparent: true, opacity, depthWrite: false });
  return new Line(geometry, material);
}

function addGlow(parent: Group, radius: number, color: string, opacity: number) {
  const glow = new Mesh(
    new SphereGeometry(radius, 24, 16),
    new MeshBasicMaterial({
      color,
      side: BackSide,
      transparent: true,
      opacity,
      blending: AdditiveBlending,
      depthWrite: false,
    }),
  );
  parent.add(glow);
  return glow;
}

function makeSatellite(
  parent: Group,
  radius: number,
  color: string,
  accent: string,
  position: Vector3,
  cratered = false,
) {
  const group = new Group();
  group.position.copy(position);
  parent.add(group);

  const texture = createBodyTexture(color, accent, cratered);
  const material = new MeshPhongMaterial({
    map: texture,
    color: 0xffffff,
    shininess: cratered ? 16 : 48,
    specular: new Color(cratered ? '#6e7581' : '#f2bec8'),
  });
  const body = new Mesh(new SphereGeometry(radius, 40, 28), material);
  body.castShadow = true;
  body.receiveShadow = true;
  group.add(body);
  addGlow(group, radius * 1.13, accent, cratered ? .08 : .12);
  const light = new PointLight(accent, cratered ? .28 : 1.4, radius * 8, 2);
  light.position.set(0, 0, radius * 1.5);
  group.add(light);
  return { group, body };
}

function makeStarField() {
  const positions: number[] = [];
  const colors: number[] = [];
  for (let star = 0; star < 110; star += 1) {
    const angle = star * 2.39996;
    const radius = 2.8 + (star * 17 % 31) / 10;
    const y = ((star * 19 % 37) - 18) / 10;
    positions.push(Math.cos(angle) * radius, y, Math.sin(angle) * radius);
    const color = new Color(star % 5 === 0 ? '#ed4658' : '#d8e2f0');
    colors.push(color.r, color.g, color.b);
  }
  const geometry = new BufferGeometry();
  geometry.setAttribute('position', new BufferAttribute(new Float32Array(positions), 3));
  geometry.setAttribute('color', new BufferAttribute(new Float32Array(colors), 3));
  return new Points(geometry, new PointsMaterial({
    size: .018,
    vertexColors: true,
    transparent: true,
    opacity: .78,
    sizeAttenuation: true,
    depthWrite: false,
  }));
}

function disposeScene(scene: Scene) {
  scene.traverse((object) => {
    if (object instanceof Mesh || object instanceof Line || object instanceof Points) {
      object.geometry.dispose();
      const materials = Array.isArray(object.material) ? object.material : [object.material];
      materials.forEach((material) => {
        if ('map' in material && material.map) material.map.dispose();
        material.dispose();
      });
    }
  });
}

export default function AcademicSolarSystem({
  totalStudents,
  highRisk,
  mediumRisk,
  lowRisk,
  reduceMotion,
}: AcademicSolarSystemProps) {
  const mountRef = useRef<HTMLDivElement>(null);
  const [webglUnavailable, setWebglUnavailable] = useState(false);
  const highShare = totalStudents ? highRisk / totalStudents : 0;
  const mediumShare = totalStudents ? mediumRisk / totalStudents : 0;

  useEffect(() => {
    const mount = mountRef.current;
    if (!mount) return;

    let renderer: WebGLRenderer;
    try {
      renderer = new WebGLRenderer({ alpha: true, antialias: true, powerPreference: 'high-performance' });
    } catch (error) {
      console.error('Unable to initialize the academic solar system WebGL renderer.', error);
      setWebglUnavailable(true);
      return;
    }

    setWebglUnavailable(false);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.75));
    renderer.outputColorSpace = SRGBColorSpace;
    renderer.toneMapping = ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.05;
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = PCFShadowMap;
    renderer.domElement.setAttribute('aria-hidden', 'true');
    renderer.domElement.className = 'solar-system-canvas';
    mount.appendChild(renderer.domElement);

    const scene = new Scene();
    const camera = new PerspectiveCamera(32, 1, .1, 60);
    camera.position.set(0, .15, 9);

    scene.add(new AmbientLight('#a9b9d1', 1.35));
    const keyLight = new DirectionalLight('#fff3f3', 2.3);
    keyLight.position.set(-4, 5, 7);
    keyLight.castShadow = true;
    keyLight.shadow.mapSize.set(1024, 1024);
    keyLight.shadow.camera.near = .5;
    keyLight.shadow.camera.far = 20;
    scene.add(keyLight);
    const silverRim = new DirectionalLight('#9fc7ff', 1.3);
    silverRim.position.set(4, 1, -5);
    scene.add(silverRim);
    const crimsonLight = new PointLight('#f20b32', 8, 12, 2);
    crimsonLight.position.set(2, 1, 3);
    scene.add(crimsonLight);

    const stars = makeStarField();
    scene.add(stars);

    const centralTexture = createRiskTexture(highRisk, mediumRisk, lowRisk, totalStudents);
    const centralPlanet = new Group();
    const centralBody = new Mesh(
      new SphereGeometry(1.34, 72, 56),
      new MeshPhongMaterial({
        map: centralTexture,
        shininess: 58,
        specular: new Color('#fff1f2'),
      }),
    );
    centralBody.castShadow = true;
    centralBody.receiveShadow = true;
    centralPlanet.add(centralBody);
    addGlow(centralPlanet, 1.39, '#e11d32', .1);
    scene.add(centralPlanet);

    const bodies: OrbitBody[] = [];
    const planets = [
      { radius: 2.18, ellipse: 2.18, height: 1.3, incline: .82, node: -.16, phase: 2.14, speed: .24, size: .29, color: '#a70d26', accent: '#ff5366' },
      { radius: 2.72, ellipse: 2.72, height: 1.58, incline: .71, node: .12, phase: 3.2, speed: -.17, size: .23, color: '#7d8797', accent: '#e4edf7' },
      { radius: 3.08, ellipse: 3.08, height: 1.78, incline: .96, node: -.08, phase: 5.28, speed: .13, size: .25, color: '#cb1730', accent: '#ff5262' },
    ];

    planets.forEach((config, index) => {
      const plane = new Group();
      plane.rotation.set(config.incline, 0, config.node);
      scene.add(plane);
      plane.add(makeOrbitPath(config.ellipse, config.height, index === 1 ? '#c6d7ee' : '#fa304a', index === 1 ? .43 : .35));

      const orbit = new Group();
      orbit.rotation.y = config.phase;
      plane.add(orbit);
      const { group: planetGroup, body } = makeSatellite(
        orbit,
        config.size,
        config.color,
        config.accent,
        new Vector3(config.radius, 0, 0),
      );
      planetGroup.rotation.y = index * 1.7;
      bodies.push({ orbit, body, speed: config.speed });

      const cometOrbit = new Group();
      cometOrbit.rotation.y = config.phase + 1.4;
      plane.add(cometOrbit);
      const comet = new Mesh(
        new SphereGeometry(.034, 16, 12),
        new MeshBasicMaterial({ color: config.accent }),
      );
      comet.position.set(config.radius, 0, 0);
      cometOrbit.add(comet);
      addGlow(cometOrbit, .09, config.accent, .32).position.copy(comet.position);
      bodies.push({ orbit: cometOrbit, body: comet, speed: config.speed * 1.38 });

      if (index === 0) {
        const moonOrbit = new Group();
        moonOrbit.position.set(config.radius, 0, 0);
        moonOrbit.rotation.x = .72;
        planetGroup.add(moonOrbit);
        moonOrbit.add(makeOrbitPath(.48, .34, '#e4e9f1', .56));
        const moonRevolution = new Group();
        moonRevolution.rotation.y = 1.2;
        moonOrbit.add(moonRevolution);
        const moon = makeSatellite(
          moonRevolution,
          .105,
          '#aeb8c5',
          '#eef2f6',
          new Vector3(.48, 0, 0),
          true,
        );
        bodies.push({ orbit: moonRevolution, body: moon.body, speed: .92 });
      }
    });

    const clock = { previous: performance.now() };
    const resize = () => {
      const width = mount.clientWidth;
      const height = mount.clientHeight;
      if (!width || !height) return;
      camera.aspect = width / height;
      camera.position.z = width / height < 1.1 ? 12.2 : width / height < 1.25 ? 10.7 : 9.8;
      camera.updateProjectionMatrix();
      renderer.setSize(width, height, false);
      if (reduceMotion) renderer.render(scene, camera);
    };
    const observer = new ResizeObserver(resize);
    observer.observe(mount);
    resize();

    if (reduceMotion) {
      renderer.render(scene, camera);
    } else {
      renderer.setAnimationLoop(() => {
        const now = performance.now();
        const delta = Math.min((now - clock.previous) / 1000, .05);
        clock.previous = now;
        centralPlanet.rotation.y += delta * .22;
        centralPlanet.rotation.x = Math.sin(now * .00014) * .025;
        bodies.forEach(({ orbit, body, speed }) => {
          orbit.rotation.y += delta * speed;
          body.rotation.y += delta * (speed > 0 ? .72 : -.61);
        });
        stars.rotation.y += delta * .012;
        renderer.render(scene, camera);
      });
    }

    return () => {
      observer.disconnect();
      renderer.setAnimationLoop(null);
      disposeScene(scene);
      renderer.dispose();
      renderer.domElement.remove();
    };
  }, [totalStudents, highRisk, mediumRisk, lowRisk, reduceMotion]);

  return (
    <>
      <div className="solar-system-stage" aria-hidden="true">
        <div ref={mountRef} className="solar-system-canvas-mount" />
        {webglUnavailable && (
          <div
            className="solar-system-fallback"
            style={{
              background: `linear-gradient(90deg, ${RISK_COLORS[0]} 0% ${highRisk / (highRisk + mediumRisk + lowRisk) * 100}%, ${RISK_COLORS[1]} ${highRisk / (highRisk + mediumRisk + lowRisk) * 100}% ${(highRisk + mediumRisk) / (highRisk + mediumRisk + lowRisk) * 100}%, ${RISK_COLORS[2]} ${(highRisk + mediumRisk) / (highRisk + mediumRisk + lowRisk) * 100}% 100%)`,
            }}
          />
        )}
        <div className="solar-system-atmosphere" />
      </div>
      <div className="solar-system-center" aria-hidden="true">
        <span className="solar-system-count">{totalStudents}</span>
        <span className="solar-system-center-label">Students</span>
      </div>
    </>
  );
}
