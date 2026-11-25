// Infrastructure smoke test formulation. NOT BM1109.
//
// 2D linear magnetostatics, a-formulation (A_z), one rectangular core and two
// coil blocks with prescribed current density. Purpose: prove that
// Gmsh -> .msh -> GetDP -> exported quantities works. No OEW-6T terminals,
// no BM1109 dimensions, no machine topology, no ROM data.
//
// Outputs (written next to this file, runner copies it into a run directory):
//   energy.txt  - magnetic energy W = int 1/(2 mu) |B|^2 dA   [J/m]
//   az_p1.txt   - A_z at the bottom corner of the core end face  [Wb/m]
//   az_p2.txt   - A_z at the top corner of the core end face     [Wb/m]
//   b.pos       - B field (for finiteness check)
//
// Flux through the core end face is A_z(p2) - A_z(p1): GetDP does not evaluate
// {d a} on 1D regions (verified: SquNorm on the same line returns 0 while the
// line integral of 1 returns its exact length), so flux is taken from A.

mu0 = 4.e-7 * Pi;

AIR = 100;
CORE = 101;
COIL_TOP = 102;
COIL_BOTTOM = 103;
BOUNDARY = 104;
CUT = 105;
CUTX = 106;

mur_core = 1000.;
J0 = 1.0e6;   // A/m^2, arbitrary smoke value

Group {
  Air    = Region[ AIR ];
  Core   = Region[ CORE ];
  CoilT  = Region[ COIL_TOP ];
  CoilB  = Region[ COIL_BOTTOM ];
  Vol_Mag     = Region[ {Air, Core, CoilT, CoilB} ];
  Vol_S0_Mag  = Region[ {CoilT, CoilB} ];
  Sur_Dir_Mag = Region[ BOUNDARY ];
  Cut  = Region[ CUT ];
  CutX = Region[ CUTX ];
}

Function {
  mu[ Air ]   = mu0;
  mu[ CoilT ] = mu0;
  mu[ CoilB ] = mu0;
  mu[ Core ]  = mur_core * mu0;

  nu[ Air ]   = 1. / mu0;
  nu[ CoilT ] = 1. / mu0;
  nu[ CoilB ] = 1. / mu0;
  nu[ Core ]  = 1. / (mur_core * mu0);

  js[ CoilT ] = Vector[ 0., 0.,  J0 ];
  js[ CoilB ] = Vector[ 0., 0., -J0 ];
}

Jacobian {
  { Name Vol;
    Case { { Region All; Jacobian Vol; } }
  }
  { Name Sur;
    Case { { Region All; Jacobian Sur; } }
  }
}

Integration {
  { Name Int;
    Case {
      { Type Gauss;
        Case {
          { GeoElement Line; NumberOfPoints 3; }
          { GeoElement Triangle; NumberOfPoints 3; }
          { GeoElement Quadrangle; NumberOfPoints 4; }
        }
      }
    }
  }
}

Constraint {
  { Name a;
    Case { { Region Sur_Dir_Mag; Value 0.; } }
  }
}

FunctionSpace {
  { Name Hcurl_a; Type Form1P;
    BasisFunction {
      { Name se; NameOfCoef ae; Function BF_PerpendicularEdge;
        Support Vol_Mag; Entity NodesOf[ All ]; }
    }
    Constraint {
      { NameOfCoef ae; EntityType NodesOf; NameOfConstraint a; }
    }
  }
}

Formulation {
  { Name MagSta_a; Type FemEquation;
    Quantity {
      { Name a; Type Local; NameOfSpace Hcurl_a; }
    }
    Equation {
      Integral { [ nu[] * Dof{d a}, {d a} ];
        In Vol_Mag; Jacobian Vol; Integration Int; }
      Integral { [ -js[], {a} ];
        In Vol_S0_Mag; Jacobian Vol; Integration Int; }
    }
  }
}

Resolution {
  { Name MagSta_a;
    System { { Name A; NameOfFormulation MagSta_a; } }
    Operation {
      InitSolution[ A ];
      Generate[ A ];
      Solve[ A ];
      SaveSolution[ A ];
    }
  }
}

PostProcessing {
  { Name PP; NameOfFormulation MagSta_a;
    Quantity {
      { Name W; Value {
          Integral { [ 1. / (2. * mu[]) * SquNorm[{d a}] ];
            In Vol_Mag; Jacobian Vol; Integration Int; }
        }
      }
      { Name az; Value {
          Term { [ CompZ[{a}] ]; In Vol_Mag; Jacobian Vol; }
        }
      }
      { Name b; Value {
          Term { [ {d a} ]; In Vol_Mag; Jacobian Vol; }
        }
      }
    }
  }
}

PostOperation {
  { Name PP; NameOfPostProcessing PP;
    Operation {
      Print[ W[Vol_Mag], OnGlobal, Format Table, File > "energy.txt" ];
      Print[ az, OnPoint { -0.04, -0.005, 0 }, Format Table, File > "az_p1.txt" ];
      Print[ az, OnPoint { -0.04,  0.005, 0 }, Format Table, File > "az_p2.txt" ];
      Print[ b, OnElementsOf Vol_Mag, File "b.pos" ];
    }
  }
}
