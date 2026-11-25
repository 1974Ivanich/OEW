// Infrastructure smoke test geometry. NOT BM1109.
// Purpose: prove Gmsh -> GetDP -> results chain works.
// Simple rectangular linear core + two coil blocks with prescribed current density.
// No OEW-6T terminals, no BM1109 dimensions, no machine topology.

lc_core = 0.002;
lc_air  = 0.010;

// Outer air box
Point(1) = {-0.10, -0.07, 0, lc_air};
Point(2) = { 0.10, -0.07, 0, lc_air};
Point(3) = { 0.10,  0.07, 0, lc_air};
Point(4) = {-0.10,  0.07, 0, lc_air};

// Linear core bar
Point(5) = {-0.04, -0.005, 0, lc_core};
Point(6) = { 0.04, -0.005, 0, lc_core};
Point(7) = { 0.04,  0.005, 0, lc_core};
Point(8) = {-0.04,  0.005, 0, lc_core};

// Coil block above the bar (current +J)
Point(9)  = {-0.03, 0.006, 0, lc_core};
Point(10) = { 0.03, 0.006, 0, lc_core};
Point(11) = { 0.03, 0.016, 0, lc_core};
Point(12) = {-0.03, 0.016, 0, lc_core};

// Coil block below the bar (current -J)
Point(13) = {-0.03, -0.016, 0, lc_core};
Point(14) = { 0.03, -0.016, 0, lc_core};
Point(15) = { 0.03, -0.006, 0, lc_core};
Point(16) = {-0.03, -0.006, 0, lc_core};

// Outer boundary
Line(1) = {1, 2};
Line(2) = {2, 3};
Line(3) = {3, 4};
Line(4) = {4, 1};

// Core rectangle
Line(5) = {5, 6};
Line(6) = {6, 7};
Line(7) = {7, 8};
Line(8) = {8, 5};

// Top coil rectangle
Line(9)  = {9, 10};
Line(10) = {10, 11};
Line(11) = {11, 12};
Line(12) = {12, 9};

// Bottom coil rectangle
Line(13) = {13, 14};
Line(14) = {14, 15};
Line(15) = {15, 16};
Line(16) = {16, 13};

Line Loop(1) = {1, 2, 3, 4};
Line Loop(2) = {5, 6, 7, 8};
Line Loop(3) = {9, 10, 11, 12};
Line Loop(4) = {13, 14, 15, 16};

// Air = box with three holes; core and coil blocks are the holes' surfaces.
Plane Surface(1) = {1, 2, 3, 4};
Plane Surface(2) = {2};
Plane Surface(3) = {3};
Plane Surface(4) = {4};

Physical Surface("Air", 100) = {1};
Physical Surface("Core", 101) = {2};
Physical Surface("CoilTop", 102) = {3};
Physical Surface("CoilBottom", 103) = {4};

Physical Line("Boundary", 104) = {1, 2, 3, 4};
// Control line for flux: bottom edge of the core (mesh entity, not a cut plane).
Physical Line("Cut", 105) = {5};
// Left end face of the core: flux entering the bar (integrand Bx).
Physical Line("CutX", 106) = {8};
