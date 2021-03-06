open Core

open Utils
open Type
open Program
open Grammar

type frontier = {
  programs: (program*float) list;
  request: tp
}

let violates_symmetry f a n = 
  if not (is_base_primitive f) then false else
    let a = application_function a in
    if not (is_base_primitive a) then false else 
      match (n, primitive_name f, primitive_name a) with
      | (0,"car","cons") -> true
      | (0,"car","empty") -> true
      | (0,"cdr","cons") -> true
      | (0,"cdr","empty") -> true
      | (_,"+","0") -> true
      | (1,"-","0") -> true
      | (0,"+","+") -> true
      | (0,"*","*") -> true
      | (_,"*","0") -> true
      | (_,"*","1") -> true
      | (0,"empty?","cons") -> true
      | (0,"empty?","empty") -> true
      | (0,"zero?","0") -> true
      | (0,"zero?","1") -> true
      | (0,"zero?","-1") -> true
      | _ -> false

(* For now this is disabled and is not used *)
let violates_commutative f x y =
    match f with
    | "eq?" | "+" -> compare_program x y > 0
    | _ -> false
      

let rec enumerate_programs' (g: grammar) (context: tContext) (request: tp) (environment: tp list)
    (lower_bound: float) (upper_bound: float)
    ?maximumDepth:(maximumDepth = 9999)
    (callBack: program -> tContext -> float -> unit) : unit =
  (* Enumerates programs satisfying: lowerBound <= MDL < upperBound *)
  (* INVARIANT: request always has the current context applied to it already *)
  if maximumDepth < 1 || upper_bound < 0.0 then () else
    match request with
    | TCon("->",[argument_type;return_type],_) ->
      let newEnvironment = argument_type :: environment in
      enumerate_programs' ~maximumDepth:maximumDepth (* ~recursion:recursion *)
        g context return_type newEnvironment
        lower_bound upper_bound
        (fun body newContext ll -> callBack (Abstraction(body)) newContext ll)

    | _ -> (* not trying to enumerate functions *)
      let candidates = unifying_expressions g environment request context in
      candidates |> 
      List.iter ~f:(fun (candidate, argument_types, context, ll) ->
          let mdl = 0.-.ll in
          if mdl >= upper_bound then () else
            enumerate_applications
              ~maximumDepth:(maximumDepth - 1)
              g context environment
              argument_types candidate 
              (lower_bound+.ll) (upper_bound+.ll)
              (fun p k al -> callBack p k (ll+.al)))
and
  enumerate_applications
    ?maximumDepth:(maximumDepth = 9999)
    (g: grammar) (context: tContext)  (environment: tp list)
    (argument_types: tp list) (f: program)
    ?originalFunction:(originalFunction=f)
    ?argumentIndex:(argumentIndex=0)
    (lower_bound: float) (upper_bound: float)
    (callBack: program -> tContext -> float -> unit) : unit =
  (* Enumerates application chains satisfying: lowerBound <= MDL < upperBound *)
  (* returns the log likelihood of the arguments! not the log likelihood of the application! *)
  if maximumDepth < 1 || upper_bound < 0.0 then () else 
    match argument_types with
    | [] -> (* not a function so we don't need any applications *)
      begin 
        if lower_bound <= 0. && 0. < upper_bound then
          (* match f with
           * | Apply(Apply(Primitive(_,function_name,_),first_argument),second_argument)
           *   when violates_commutative function_name first_argument second_argument -> ()
           * | _ -> *) callBack f context 0.0
        else ()
      end
    | first_argument::later_arguments ->
      let (context,first_argument) = applyContext context first_argument in
      enumerate_programs' ~maximumDepth:maximumDepth (* ~recursion:NoRecursion *)
        g context first_argument environment
        0. upper_bound
        (fun a k ll ->
           if violates_symmetry originalFunction a argumentIndex then () else 
             let a = Apply(f,a) in
             enumerate_applications
               ~originalFunction:originalFunction
               ~argumentIndex:(argumentIndex+1)
               ~maximumDepth:maximumDepth
               g k environment
               later_arguments a
               (lower_bound+.ll) (upper_bound+.ll)
               (fun a k a_ll -> callBack a k (a_ll+.ll)))

let enumerate_programs g request lb ub k =
  let number_of_arguments = arguments_of_type request |> List.length in
  let definitely_recursive = grammar_has_recursion number_of_arguments g in

  (* Strip out the recursion operators because they only occur at the top level *)
  let g' = {logVariable = g.logVariable;
            library =
              g.library |>
              List.filter ~f:(fun (p,_,_,_) -> not (is_recursion_primitive p)) |>
              (* sort library by number of arguments so that it will tend to explore shorter things first *)
              List.sort ~cmp:(fun (_,a,_,_) (_,b,_,_) -> List.length (arguments_of_type a) - List.length (arguments_of_type b)) } in

  let request' =
    if definitely_recursive then request @> request else request
  in

  let k' =
    if definitely_recursive then begin 
      fun p _ l ->
        let p' = 
          match p with
          | Abstraction(body) ->
            if variable_is_bound ~height:0 body then (* Used the fix point operator *)
              match number_of_arguments with
              | 1 ->
                Abstraction(Apply(Apply(primitive_recursion, Index(0)),p))
              | 2 -> Abstraction(Abstraction(Apply(Apply(Apply(primitive_recursion2, Index(1)),Index(0)),p)))
              | _ -> raise (Failure "number_of_arguments not supported by fix point")
            else body (* Remove unused recursion *)
          | _ -> raise (Failure "enumerated recursive definition that does not start with a lambda")
        in k p' l
    end
    else begin
      fun p _ l -> k p l
    end
  in
  
  enumerate_programs' g' empty_context request' [] lb ub k'


let test_recursive_enumeration () =
  let g = primitive_grammar [primitive_cons;primitive_car;primitive_cdr;primitive_is_empty;
                             primitive_empty;
                             primitive0;
                             primitive_recursion;primitive_recursion2;] in
  let request = (tlist tint @> tint @> tlist tint) in
  enumerate_programs g request 0. 15.
    (fun p l ->
       Printf.printf "%s\t%f\n"
         (string_of_program p)
         l;
       flush_everything();
       let t = infer_program_type empty_context [] p |> snd in
       ignore(unify empty_context t request);
    Printf.printf "%s\n" (t |> string_of_type))
;;

(* test_recursive_enumeration ();; *)
