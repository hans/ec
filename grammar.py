from collections import defaultdict

import json

from .frontier import *
from .program import *
from .type import *
from .utilities import *

import time

class GrammarFailure(Exception): pass
class NoCandidates(Exception): pass

class Grammar(object):
    def __init__(self, logVariable, productions):
        self.logVariable = logVariable
        self.productions = productions

        self.expression2likelihood = dict( (p,l) for l,_,p in productions)
        self.expression2likelihood[Index(0)] = self.logVariable

    @staticmethod
    def fromProductions(productions, logVariable=0.0):
        """Make a grammar from primitives and their relative logpriors."""
        return Grammar(logVariable, [(l, p.infer(), p) for l, p in productions])

    @staticmethod
    def uniform(primitives):
        return Grammar(0.0, [(0.0,p.infer(),p) for p in primitives ])

    def __len__(self): return len(self.productions)
    def __str__(self):
        def productionKey(xxx_todo_changeme):
            (l,t,p) = xxx_todo_changeme
            return not isinstance(p,Primitive), -l
        lines = ["%f\tt0\t$_"%self.logVariable]
        for l,t,p in sorted(self.productions, key=productionKey):
            l = "%f\t%s\t%s"%(l,t,p)
            if not t.isArrow() and isinstance(p,Invented):
                try: l += "\teval = %s"%(p.evaluate([]))                    
                except: pass

            lines.append(l)
        return "\n".join(lines)

    @property
    def primitives(self):
        return [p for _, _, p in self.productions]

    def removeProductions(self,ps):
        return Grammar(self.logVariable,
                       [(l,t,p) for (l,t,p) in self.productions if not p in ps ])

    def buildCandidates(self, request, context, environment,
                        # Should the log probabilities be normalized?
                        normalize=True,
                        # Should be returned a table mapping primitives to their candidate entry?
                        returnTable=False,
                        # Should we return probabilities vs log probabilities?
                        returnProbabilities=False,
                        # Must be a leaf (have no arguments)?
                        mustBeLeaf=False):
        """Primitives that are candidates for being used given a requested type
        If returnTable is false (default): returns [((log)likelihood, tp, primitive, context)]
        if returntable is true: returns {primitive: ((log)likelihood, tp, context)}"""
        if returnProbabilities: assert normalize
        
        candidates = []
        variableCandidates = []
        for l,t,p in self.productions:
            try:
                newContext, t = t.instantiate(context)
                newContext = newContext.unify(t.returns(), request)
                t = t.apply(newContext)
                if mustBeLeaf and t.isArrow(): continue
                candidates.append((l,t,p,newContext))
            except UnificationFailure: continue
        for j,t in enumerate(environment):
            try:
                newContext = context.unify(t.returns(), request)
                t = t.apply(newContext)
                if mustBeLeaf and t.isArrow(): continue
                variableCandidates.append((t, Index(j), newContext))
            except UnificationFailure: continue

        candidates += [ (self.logVariable - log(len(variableCandidates)), t, p, k)
                        for t,p,k in variableCandidates ]
        if candidates == []: raise NoCandidates()
        
        if normalize:
            z = lse([ l for l,t,p,k in candidates ])
            if returnProbabilities: candidates = [ (exp(l - z), t, p, k) for l,t,p,k in candidates ]
            else: candidates = [ (l - z, t, p, k) for l,t,p,k in candidates ]

        if returnTable:
            return {p: (l,t,k) for l,t,p,k in candidates }
        else:
            return candidates

    def sample(self, request, maximumDepth=3):
        while True:
            try:
                _,e = self._sample(request, Context.EMPTY, [], maximumDepth=maximumDepth)
                return e
            except NoCandidates: continue
    def _sample(self, request, context, environment, maximumDepth):
        if request.isArrow():
            context, expression = self._sample(request.arguments[1],
                                               context,
                                               [request.arguments[0]] + environment,
                                               maximumDepth)
            return context, Abstraction(expression)

        candidates = self.buildCandidates(request, context, environment,
                                          normalize=True,
                                          returnProbabilities=True,
                                          # Force it to terminate in a
                                          # leaf; a primitive with no
                                          # function arguments
                                          mustBeLeaf=maximumDepth <= 1)
        newType, chosenPrimitive, context = sampleDistribution(candidates)

        # Sample the arguments
        xs = newType.functionArguments()
        returnValue = chosenPrimitive

        for x in xs:
            x = x.apply(context)
            context, x = self._sample(x, context, environment, maximumDepth - 1)
            returnValue = Application(returnValue, x)

        return context, returnValue        
        

    def likelihoodSummary(self, context, environment, request, expression, silent=False):
        if request.isArrow():
            if not isinstance(expression,Abstraction):
                if not silent:
                    eprint("Request is an arrow but I got",expression)
                return context,None
            return self.likelihoodSummary(context,
                                          [request.arguments[0]] + environment,
                                          request.arguments[1],
                                          expression.body,
                                          silent=silent)
        # Build the candidates
        candidates = self.buildCandidates(request, context, environment,
                                          normalize=False,
                                          returnTable=True)
        
        # A list of everything that would have been possible to use here
        possibles = [ p for p in candidates.keys() if not p.isIndex ]
        numberOfVariables = sum(p.isIndex for p in candidates.keys())
        if numberOfVariables > 0: possibles += [Index(0)]

        f,xs = expression.applicationParse()

        if f not in candidates:
            if not silent:
                eprint(f,"Not in candidates")
                # eprint("Candidates is",candidates)
                eprint("request is",request)
                eprint("xs",xs)
                eprint("environment",environment)
                assert False
            return context,None

        thisSummary = LikelihoodSummary()
        thisSummary.record(f, possibles,
                           constant = -math.log(numberOfVariables) if f.isIndex else 0)

        _, tp, context = candidates[f]
        argumentTypes = tp.functionArguments()
        if len(xs) != len(argumentTypes):
            eprint("PANIC: not enough arguments for the type")
            eprint("request",request)
            eprint("tp",tp)
            eprint("expression",expression)
            eprint("xs",xs)
            eprint("argumentTypes",argumentTypes)
            # This should absolutely never occur
            raise GrammarFailure((context, environment, request, expression))            

        for argumentType, argument in zip(argumentTypes, xs):
            argumentType = argumentType.apply(context)
            context, newSummary = self.likelihoodSummary(context, environment, argumentType,
                                                         argument, silent=silent)
            if newSummary is None: return context, None
            thisSummary.join(newSummary)

        return context, thisSummary

    def bestFirstEnumeration(self, request):
        from heapq import heappush, heappop
        
        pq = []

        def choices(parentCost, xs):
            for c,x in xs: heappush(pq, (parentCost+c,x))

        def g(parentCost, request, _=None,
              context=None, environment=[],
              k=None):
            """
            k is a continuation. 
            k: Expects to be called with MDL, context, expression.
            """
            
            assert k is not None
            if context is None: context = Context.EMPTY

            if request.isArrow():
                g(parentCost, request.arguments[1],
                  context = context,
                  environment = [request.arguments[0]] + environment,
                  k = lambda MDL, newContext, p: k(MDL, newContext, Abstraction(p)))
            else:            
                candidates = self.buildCandidates(request, context, environment,
                                                  normalize=True,
                                                  returnProbabilities=False,
                                                  returnTable=True)
                choices(parentCost,
                        [(-f_ll_tp_newContext[1][0],
                             lambda: ga(parentCost-f_ll_tp_newContext[1][0], f_ll_tp_newContext[0], f_ll_tp_newContext[1][1].functionArguments(),
                                        context=f_ll_tp_newContext[1][2], environment=environment,
                                        k=k)) for f_ll_tp_newContext in iter(candidates.items())])
        def ga(costSoFar, f, argumentTypes, _=None,
               context=None, environment=None,
               k=None):
            if argumentTypes == []:
                k(costSoFar, context, f)
            else:
                t1 = argumentTypes[0].apply(context)
                g(costSoFar, t1, context=context, environment=environment,
                  k=lambda newCost, newContext, argument: \
                  ga(newCost, Application(f,argument), argumentTypes[1:],
                     context=newContext, environment=environment,
                     k=k))

        def receiveResult(MDL, _, expression):
            heappush(pq, (MDL, expression))


        g(0., request, context=Context.EMPTY, environment=[], k=receiveResult)
        frontier = []
        while len(frontier) < 10**3:
            MDL, action = heappop(pq)
            if isinstance(action, Program):
                expression = action
                frontier.append(expression)
                #eprint("Enumerated program",expression,-MDL,self.closedLogLikelihood(request, expression))
            else:
                action()


    def closedLikelihoodSummary(self, request, expression, silent=False):
        try:
            context, summary = self.likelihoodSummary(Context.EMPTY, [], request,
                                                      expression, silent=silent)
        except GrammarFailure as e:
            failureExport = 'failures/grammarFailure%s.pickle'%(time.time() + getPID())
            eprint("PANIC: Grammar failure, exporting to ",failureExport)
            with open(failureExport,'wb') as handle:
                pickle.dump((e,self,request,expression), handle)
            assert False
            
        return summary

    def logLikelihood(self, request, expression):
        summary = self.closedLikelihoodSummary(request, expression)
        if summary is None:
            eprint("FATAL: program [ %s ] does not have a likelihood summary."%expression,"r = ",request,"\n",self)
            assert False
        return summary.logLikelihood(self)

    def rescoreFrontier(self, frontier):
        return Frontier([ FrontierEntry(e.program,
                                        logPrior=self.logLikelihood(frontier.task.request, e.program),
                                        logLikelihood=e.logLikelihood)
                          for e in frontier ],
                        frontier.task)

    def productionUses(self, frontiers):
        """Returns the expected number of times that each production was used. {production: expectedUses}"""
        frontiers = [ self.rescoreFrontier(f).normalize() for f in frontiers if not f.empty ]
        uses = {p: 0. for p in self.primitives }
        for f in frontiers:
            for e in f:
                summary = self.closedLikelihoodSummary(f.task.request,
                                                       e.program)
                for p,u in summary.uses:
                    uses[p] += u*math.exp(e.logPosterior)
        return uses        

    def smartlyInitialize(self, expectedSize):
        frequencies = {}
        for _,t,p in self.productions:
            a = len(t.functionArguments())
            frequencies[a] = frequencies.get(a,0) + 1
        return Grammar(-log(frequencies[0]),
                       [(-log(frequencies[len(t.functionArguments())]) - len(t.functionArguments())*expectedSize,
                         t,p) for l,t,p in self.productions ])

    def enumeration(self, context, environment, request, upperBound, maximumDepth=20, lowerBound=0.):
        '''Enumerates all programs whose MDL satisfies: lowerBound < MDL <= upperBound'''
        if upperBound <= 0 or maximumDepth == 1: return 

        if request.isArrow():
            v = request.arguments[0]
            for l, newContext, b in self.enumeration(context, [v] + environment,
                                                     request.arguments[1],
                                                     upperBound=upperBound,
                                                     lowerBound=lowerBound,
                                                     maximumDepth=maximumDepth):
                yield l, newContext, Abstraction(b)

        else:
            candidates = self.buildCandidates(request, context, environment,
                                              normalize=True)

            for l, t, p, newContext in candidates:
                mdl = -l
                if not (mdl <= upperBound): continue

                xs = t.functionArguments()
                for aL,aK,application in\
                    self.enumerateApplication(newContext, environment, p, xs,
                                              upperBound=upperBound + l,
                                              lowerBound=lowerBound + l,
                                              maximumDepth=maximumDepth - 1):
                    yield aL+l, aK, application

    def enumerateApplication(self, context, environment,
                             function, argumentRequests,
                             # Upper bound on the description length of all of the arguments
                             upperBound,
                             # Lower bound on the description length of all of the arguments
                             lowerBound=0.,
                             maximumDepth=20,
                             originalFunction=None,
                             argumentIndex=0):
        if upperBound <= 0 or maximumDepth == 1: return
        if originalFunction is None: originalFunction = function

        if argumentRequests == []:
            if lowerBound < 0. and 0. <= upperBound:
                yield 0., context, function
            else: return 
        else:
            argRequest = argumentRequests[0].apply(context)
            laterRequests = argumentRequests[1:]
            for argL, newContext, arg in self.enumeration(context, environment, argRequest,
                                                          upperBound=upperBound,
                                                          lowerBound=0.,
                                                          maximumDepth=maximumDepth):
                if violatesSymmetry(originalFunction, arg, argumentIndex): continue
                
                newFunction = Application(function, arg)
                for resultL, resultK, result in self.enumerateApplication(newContext, environment, newFunction,
                                                                          laterRequests,
                                                                          upperBound=upperBound + argL,
                                                                          lowerBound=lowerBound + argL,
                                                                          maximumDepth=maximumDepth,
                                                                          originalFunction=originalFunction,
                                                                          argumentIndex=argumentIndex+1):
                    yield resultL + argL, resultK, result

    def enumerateNearby(self, request, expr, distance=3.0):
        """Enumerate programs with local mutations in subtrees with small description length"""
        if distance <= 0:
            yield expr
        else:
            def mutations(tp, loss):
                for _, _, expr in self.enumeration(Context.EMPTY, [], tp, distance-loss):
                    yield expr
            yield from Mutator(self, mutations).execute(expr, request)


class LikelihoodSummary(object):
    '''Summarizes the terms that will be used in a likelihood calculation'''
    def __init__(self):
        self.uses = {}
        self.normalizers = {}
        self.constant = 0.
    def __str__(self):
        return """LikelihoodSummary(constant = %f, 
uses = {%s},
normalizers = {%s})"""%(self.constant,
                        ", ".join("%s: %d"%(k,v) for k,v in self.uses.items() ),
                        ", ".join("%s: %d"%(k,v) for k,v in self.normalizers.items() ))
    def record(self, actual, possibles, constant=0.):
        # Variables are all normalized to be $0
        if isinstance(actual, Index): actual = Index(0)

        # Make it something that we can hash
        possibles = frozenset(sorted(possibles, key=hash))
        
        self.constant += constant
        self.uses[actual] = self.uses.get(actual,0) + 1
        self.normalizers[possibles]  = self.normalizers.get(possibles,0) + 1
    def join(self, other):
        self.constant += other.constant
        for k,v in other.uses.items(): self.uses[k] = self.uses.get(k,0) + v
        for k,v in other.normalizers.items(): self.normalizers[k] = self.normalizers.get(k,0) + v
    def logLikelihood(self, grammar):
        return self.constant + \
            sum(count * grammar.expression2likelihood[p] for p, count in self.uses.items() ) - \
            sum(count * lse([grammar.expression2likelihood[p] for p in ps ])
                for ps, count in self.normalizers.items() )
            
            

        
class Uses(object):
    '''Tracks uses of different grammar productions'''
    def __init__(self, possibleVariables=0., actualVariables=0.,
                 possibleUses={}, actualUses={}):
        self.actualVariables = actualVariables
        self.possibleVariables = possibleVariables
        self.possibleUses = possibleUses
        self.actualUses = actualUses

    def __str__(self):
        return "Uses(actualVariables = %f, possibleVariables = %f, actualUses = %s, possibleUses = %s)"%\
            (self.actualVariables, self.possibleVariables, self.actualUses, self.possibleUses)
    def __repr__(self): return str(self)

    def __mul__(self,a):
        return Uses(a*self.possibleVariables,
                    a*self.actualVariables,
                    {p: a*u for p,u in self.possibleUses.items() },
                    {p: a*u for p,u in self.actualUses.items() })
    def __imul__(self,a):
        self.possibleVariables *= a
        self.actualVariables *= a
        for p in self.possibleUses:
            self.possibleUses[p] *= a
        for p in self.actualUses:
            self.actualUses[p] *= a
        return self
    def __rmul__(self,a):
        return self*a
    def __radd__(self,o):
        if o == 0: return self
        return self + o
    def __add__(self,o):
        if o == 0: return self
        def merge(x,y):
            z = x.copy()
            for k,v in y.items():
                z[k] = v + x.get(k,0.)
            return z
        return Uses(self.possibleVariables + o.possibleVariables,
                    self.actualVariables + o.actualVariables,
                    merge(self.possibleUses,o.possibleUses),
                    merge(self.actualUses,o.actualUses))
    def __iadd__(self,o):
        self.possibleVariables += o.possibleVariables
        self.actualVariables += o.actualVariables
        for k, v in o.possibleUses.items():
            self.possibleUses[k] = self.possibleUses.get(k, 0.) + v
        for k, v in o.actualUses.items():
            self.actualUses[k] = self.actualUses.get(k, 0.) + v
        return self

    @staticmethod
    def join(z, *weightedUses):
        """Consumes weightedUses"""
        if not weightedUses: Uses.empty
        if len(weightedUses) == 1: return weightedUses[0][1]
        for w, u in weightedUses:
            u *= exp(w - z)
        total = Uses()
        total.possibleVariables = sum(u.possibleVariables for _, u in weightedUses)
        total.actualVariables = sum(u.actualVariables for _, u in weightedUses)
        total.possibleUses = defaultdict(float)
        total.actualUses = defaultdict(float)
        for _, u in weightedUses:
            for k, v in u.possibleUses.items():
                total.possibleUses[k] += v
            for k, v in u.actualUses.items():
                total.actualUses[k] += v
        return total
    
Uses.empty = Uses()

def violatesSymmetry(f,x,argumentIndex):
    if not f.isPrimitive: return False
    while x.isApplication: x = x.f
    if not x.isPrimitive: return False
    f = f.name
    x = x.name
    if f == "car": return x == "cons" or x == "empty"
    if f == "cdr": return x == "cons" or x == "empty"
    if f == "+": return x == "0" or (argumentIndex == 1 and x == "+")
    if f == "-": return argumentIndex == 1 and x == "0"
    if f == "empty?": return x == "cons" or x == "empty"
    if f == "zero?": return x == "0" or x == "1"
    return False
        
