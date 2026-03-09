# # Integer, String, float, boolean
# name  ="Soham";
# print(name,name+"34");
# print(type(name));
# a =10;
# b =20;

# # print(a==b, a != b, a and b, a or b, a**b, a/b, a%b);

# # for i in range(5):
# #     print(i);

# # count = 0;
# # while count < 10:
# #     print(count);
# #     count += 1;


# # List [ ], tuple (), set {}, dict {k:v}

# ls = [23,45,22];
# print(ls,ls[0]);
# # ls.clear();
# # print(ls)
# ls.append(34);
# ls.insert(0,2344);
# ls.extend([344,2111,9099]);
# print(ls);

# ls.remove(344);
# ls.pop();
# print(ls);

# isinList =  23 in ls;
# print(isinList, ls.sort(), ls.sort(reverse=True));
# updatedlist = ls.sort();
# print(updatedlist);


# Puython is interpreted language.
# run every code line by line
# print("Tutorial");
# Concurrency control of the program, threads can't run in parallel.., GIL
# Search order when look for any variable
# L- Local, E - Enclosing, G - Global, B- Built In

# outerVariable = "Python"
# def outFunc():
#     outervariable = "Changed"

#     def innerFunc():
#         outerVariable = "Inner"
#         print(outerVariable);
    
#     innerFunc();
#     print(outerVariable);

# outFunc();
# print(outerVariable);
# Reference counting, Cyclic garbage collector

import sys;
import math;

# x =  [1,2,3]; #1
# y = x;
# z = x;
# print(sys.getrefcount(x));

# export PS1="(venv) % "
# import gc

# class Node:
#     def __init__(self,name):
#         self.name = name;
#         self.next = None;

# a = Node("A");
# b = Node("B");

# a.next = b;
# b.next = a;

# print(sys.getrefcount(a),sys.getrefcount(b));

# del a.next
# del b.next
# print(sys.getrefcount(a), sys.getrefcount(b))
# print(0.2 + 0.4);

# print(math.inf,math.nan, math.isnan(math.nan));

# a =  323;
# b = 2332;
# print(id(a),id(b))

# is_list = [12,12,22,44,444,444,44,44,55]
# print(set(is_list))
# print(tuple(is_list))
# print(list(set(is_list)))

# list_join = ["A","B","C","D"]

# print('345'.join(list_join))

# st = "In A BMW Car"

# print(st.find("In"),st.split(),st.capitalize(),st.count("I"))

# Number operations

# x = 20
# print(x//5, x**3)


# So basically we  will craerte list
# list_container = [];
# for i in range(1,5):
#     list_container.append(i ** 2)
# print(list_container)

# num_range = [i for i in range(1,500) if(i%3 == 0)]
# print(num_range)