(* Copyright (c) 2016-present, Facebook, Inc.
 *
 * This source code is licensed under the MIT license found in the
 * LICENSE file in the root directory of this source tree. *)

open Ast
module Callable = Type.Callable

module ProtocolAssumptions : sig
  type t

  val find_assumed_protocol_parameters
    :  candidate:Type.t ->
    protocol:Identifier.t ->
    t ->
    Type.OrderedTypes.t option

  val add
    :  candidate:Type.t ->
    protocol:Identifier.t ->
    protocol_parameters:Type.OrderedTypes.t ->
    t ->
    t

  val empty : t
end

module CallableAssumptions : sig
  (* This should be removed when classes can be generic over TParams, and Callable can be treated
     like any other generic protocol. *)
  type t

  val find_assumed_callable_type : candidate:Type.t -> t -> Type.t option

  val add : candidate:Type.t -> callable:Type.t -> t -> t

  val empty : t
end

type t = {
  protocol_assumptions: ProtocolAssumptions.t;
  callable_assumptions: CallableAssumptions.t;
}
[@@deriving compare, sexp, hash, show]
